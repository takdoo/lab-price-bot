import os
import json
import io
import logging
import math
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
import cairosvg
import jdatetime
from PIL import Image, ImageDraw, ImageFont
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
CHAT_ID = (os.getenv("CHAT_ID") or "@lab_chats").strip()
BASE_IMAGE = "lab-price-bot.svg"
OUTPUT_IMAGE = "lab_live.jpg"
PRICE_API = "https://api.coingecko.com/api/v3/simple/price?ids=lab&vs_currencies=usd&include_24hr_change=true&include_last_updated_at=true"
HISTORY_API = "https://api.coingecko.com/api/v3/coins/lab/market_chart?vs_currency=usd&days=1"
MAX_PRICE_AGE_SECONDS = 15 * 60
REQUEST_TIMEOUT = (10, 25)
TELEGRAM_MAX_ATTEMPTS = 4
TELEGRAM_RETRY_SECONDS = 5

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def build_session():
    retry = Retry(
        total=3, connect=3, read=3, status=3, backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": "LAB-Price-Bot/2.0"})
    return session


HTTP = build_session()


def get_font(size):
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ):
        if os.path.exists(path):
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def fit_font(draw, text, max_width, initial_size, min_size=16):
    size = max(min_size, int(initial_size))
    while size > min_size:
        font = get_font(size)
        bounds = draw.textbbox((0, 0), text, font=font)
        if bounds[2] - bounds[0] <= max_width:
            return font
        size -= 2
    return get_font(min_size)


def get_price_color(change):
    if change is None or change == 0:
        return (255, 255, 255)
    return (35, 230, 130) if change > 0 else (255, 75, 90)


def get_market_data():
    response = HTTP.get(PRICE_API, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    data = response.json()
    coin = data.get("lab")
    if not isinstance(coin, dict):
        raise ValueError("CoinGecko پاسخ LAB را برنگرداند؛ شناسه ارز را بررسی کنید")

    raw_price = coin.get("usd")
    if raw_price is None:
        raise ValueError("قیمت LAB در پاسخ CoinGecko وجود ندارد")
    price = float(raw_price)
    if not math.isfinite(price) or price <= 0:
        raise ValueError(f"قیمت نامعتبر دریافت شد: {raw_price!r}")

    raw_change = coin.get("usd_24h_change")
    change = None if raw_change is None else float(raw_change)
    if change is not None and not math.isfinite(change):
        change = None

    updated_at = coin.get("last_updated_at")
    if updated_at:
        age = datetime.now(timezone.utc).timestamp() - float(updated_at)
        if age > MAX_PRICE_AGE_SECONDS:
            raise ValueError(f"قیمت دریافتی قدیمی است ({int(age)} ثانیه)؛ انتشار متوقف شد")
    return price, change


def get_history():
    response = HTTP.get(HISTORY_API, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    prices = response.json().get("prices")
    if not isinstance(prices, list):
        raise ValueError("ساختار تاریخچه قیمت نامعتبر است")

    clean = []
    for item in prices:
        try:
            value = float(item[1])
            if math.isfinite(value) and value > 0:
                clean.append(value)
        except (TypeError, ValueError, IndexError):
            continue
    if len(clean) < 2:
        raise ValueError("داده کافی برای نمودار ۲۴ ساعته دریافت نشد")
    return clean


def draw_chart(draw, prices, x1, y1, x2, y2, color):
    if len(prices) < 2:
        return
    minimum, maximum = min(prices), max(prices)
    if not math.isfinite(minimum) or not math.isfinite(maximum):
        return
    points = []
    spread = maximum - minimum
    for index, price in enumerate(prices):
        x = x1 + (index / (len(prices) - 1)) * (x2 - x1)
        y = (y1 + y2) / 2 if spread == 0 else y2 - ((price - minimum) / spread) * (y2 - y1)
        points.append((int(x), int(y)))
    draw.line(points, fill=color, width=max(2, int((x2 - x1) / 90)), joint="curve")


def create_image(price, change, history):
    if not os.path.isfile(BASE_IMAGE):
        raise FileNotFoundError(f"فایل طراحی پیدا نشد: {BASE_IMAGE}")

    # SVG gives us a crisp, modern glass dashboard at every output size.
    png_bytes = cairosvg.svg2png(url=BASE_IMAGE, output_width=1536, output_height=1024)
    with Image.open(io.BytesIO(png_bytes)) as source:
        image = source.convert("RGB")

    draw = ImageDraw.Draw(image)
    width, height = image.size
    white, green, red, muted = (245, 250, 255), (35, 230, 130), (255, 75, 90), (145, 175, 190)
    price_color = get_price_color(change)

    # Live Shamsi timestamp.
    date_text, time_text = get_iran_time()
    time_font = fit_font(draw, f"{date_text}  {time_text}", 270, 24, 16)
    draw.text((1300, 183), f"{date_text}  {time_text}", font=time_font, fill=white, anchor="mm")

    # Oversized live price; its color follows the 24-hour market direction.
    price_text = f"${price:.5f}"
    price_font = fit_font(draw, price_text, 980, 190, 70)
    draw.text((768, 425), price_text, font=price_font, fill=price_color,
              anchor="mm", stroke_width=2, stroke_fill=(0, 0, 0))

    if change is None:
        change_text = "24H CHANGE: N/A"
        change_color = muted
    elif change > 0:
        change_text = f"▲  +{change:.2f}%   |   24H"
        change_color = green
    elif change < 0:
        change_text = f"▼  {change:.2f}%   |   24H"
        change_color = red
    else:
        change_text = "—  0.00%   |   24H"
        change_color = white

    change_font = fit_font(draw, change_text, 850, 58, 30)
    draw.text((768, 548), change_text, font=change_font, fill=change_color, anchor="mm")

    # 24-hour high and low are calculated from the fetched price history.
    valid_history = [float(x) for x in history if isinstance(x, (int, float)) and math.isfinite(float(x)) and float(x) > 0]
    if valid_history:
        high_text = f"${max(valid_history):.5f}"
        low_text = f"${min(valid_history):.5f}"
    else:
        high_text = low_text = "N/A"
    stat_font = fit_font(draw, high_text, 250, 38, 22)
    draw.text((415, 720), high_text, font=stat_font, fill=green, anchor="mm")
    draw.text((768, 720), low_text, font=stat_font, fill=red, anchor="mm")
    source_font = get_font(26)
    draw.text((1121, 720), "COINGECKO", font=source_font, fill=white, anchor="mm")

    # A fresh 24-hour sparkline is drawn on every run.
    if len(valid_history) >= 2:
        chart_color = green if valid_history[-1] >= valid_history[0] else red
        draw_chart(draw, valid_history, 235, 850, 1300, 900, chart_color)

    image.save(OUTPUT_IMAGE, "JPEG", quality=94, optimize=True)
    return OUTPUT_IMAGE

def get_iran_time():
    now = datetime.now(ZoneInfo("Asia/Tehran"))
    jalali = jdatetime.datetime.fromgregorian(datetime=now)
    return jalali.strftime("%Y/%m/%d"), now.strftime("%H:%M:%S")


def make_market_comment(change, history):
    """Informal pressure/trend read from price movement, not actual buyer counts."""
    recent_change = None
    if isinstance(history, list) and len(history) >= 4:
        try:
            earlier = float(history[max(0, int(len(history) * 0.75))])
            latest = float(history[-1])
            if earlier > 0 and math.isfinite(earlier) and math.isfinite(latest):
                recent_change = ((latest - earlier) / earlier) * 100
        except (TypeError, ValueError, IndexError):
            recent_change = None

    if change is None or recent_change is None:
        return "فعلاً از داده‌های موجود نمی‌شه فهمید کفه خرید سنگین‌تره یا فروش؛ برای پیش‌بینی هم عجله نکنیم."
    if recent_change >= 1.0 and change > 0:
        return "فعلاً فشار خرید بیشتره و حرکت کوتاه‌مدت رو به بالاست؛ شانس ادامه رشد هست، ولی تثبیت قیمت مهمه."
    if recent_change <= -1.0 and change < 0:
        return "فعلاً فشار فروش بیشتره و حرکت کوتاه‌مدت رو به پایینه؛ احتمال ادامه ضعف هست، مگر اینکه خریدارها وارد بشن."
    if recent_change >= 1.0 and change <= 0:
        return "توی چند ساعت اخیر خریدارها یه تکونی دادن، ولی جمع ۲۴ ساعت هنوز مثبته نشده؛ فعلاً برگشت قطعی نیست."
    if recent_change <= -1.0 and change >= 0:
        return "با اینکه جمع ۲۴ ساعت مثبته، توی حرکت اخیر فروشنده‌ها فعال‌تر شدن؛ مراقب ضعیف شدن روند باشیم."
    if change > 0:
        return "بازار یه مقدار مثبته، ولی زور خریدارها هنوز اون‌قدر واضح نیست که بگیم رشد ادامه‌داره."
    if change < 0:
        return "بازار یه مقدار منفیه، ولی فشار خرید و فروش نزدیک به همه؛ فعلاً جهت بعدی معلوم نیست."
    return "فعلاً بازار تقریباً بی‌حرکته؛ صبر کنیم تا کفه خرید یا فروش مشخص‌تر بشه."


def make_caption(price, change, history=None):
    date_text, time_text = get_iran_time()
    if change is None:
        status, change_text = "⚪ وضعیت بازار: نامشخص", "نامشخص"
    elif change > 0:
        status, change_text = "🟢 بازار ۲۴ ساعته مثبته", f"+{change:.2f}%"
    elif change < 0:
        status, change_text = "🔴 بازار ۲۴ ساعته منفیه", f"{change:.2f}%"
    else:
        status, change_text = "⚪ بازار بدون تغییره", "0.00%"

    comment = make_market_comment(change, history)
    return (
        "🧪 <b>گزارش لحظه‌ای LAB</b>\n"
        f"💰 <b>قیمت: {chr(36)}{price:.5f}</b>\n"
        f"📊 تغییر ۲۴ ساعته: <b>{change_text}</b>  |  {status}\n\n"
        "🗣️ <b>حرفِ بازار به زبون خودمونی:</b>\n"
        f"{comment}\n\n"
        f"📅 تاریخ: {date_text}\n"
        f"🕒 ساعت ایران: {time_text}\n"
        "🔄 بروزرسانی خودکار هر ۵ دقیقه\n"
        "👨‍💻 طراح: علی حامدی\n"
        "👥 @lab_chats"
    )


def telegram_api(method, payload=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    response = HTTP.post(url, json=payload or {}, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    result = response.json()
    if not result.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {result.get('description', 'unknown error')}")
    return result.get("result")


def send_to_telegram(image_path, caption):
    if not BOT_TOKEN:
        raise ValueError("توکن ربات در GitHub Secrets تنظیم نشده است")
    if not CHAT_ID:
        raise ValueError("CHAT_ID خالی است")

    # Read the current pinned message before publishing the next LAB update.
    # After the new post is pinned, remove the previous pin only if it is
    # demonstrably an older LAB post authored by this same bot.
    previous_pinned = None
    bot_id = None
    try:
        me = telegram_api("getMe")
        bot_id = me.get("id")
        chat = telegram_api("getChat", {"chat_id": CHAT_ID})
        previous_pinned = chat.get("pinned_message")
    except Exception:
        logging.exception("نتوانستم پیام سنجاق‌شده قبلی را بررسی کنم؛ ارسال پیام جدید ادامه پیدا می‌کند")

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    last_error = None
    for attempt in range(1, TELEGRAM_MAX_ATTEMPTS + 1):
        try:
            with open(image_path, "rb") as photo:
                response = HTTP.post(
                    url,
                    data={"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"},
                    files={"photo": photo},
                    timeout=REQUEST_TIMEOUT
                )
            try:
                result = response.json()
            except ValueError as error:
                if response.status_code == 429 or response.status_code >= 500:
                    last_error = RuntimeError(f"پاسخ موقت HTTP از تلگرام: {response.status_code}")
                    if attempt < TELEGRAM_MAX_ATTEMPTS:
                        time.sleep(TELEGRAM_RETRY_SECONDS * attempt)
                        continue
                    break
                response.raise_for_status()
                raise RuntimeError("پاسخ تلگرام JSON معتبر نیست") from error

            if not result.get("ok"):
                description = str(result.get("description", "خطای نامشخص تلگرام"))
                error_code = int(result.get("error_code", response.status_code))
                last_error = RuntimeError(f"خطای تلگرام {error_code}: {description}")
                if error_code == 429 or error_code >= 500:
                    retry_after = (result.get("parameters") or {}).get("retry_after", TELEGRAM_RETRY_SECONDS)
                    if attempt < TELEGRAM_MAX_ATTEMPTS:
                        time.sleep(min(max(int(retry_after), 1), 60))
                        continue
                raise last_error

            new_message = result.get("result") or {}
            new_message_id = new_message.get("message_id")
            logging.info("پیام جدید LAB ارسال شد؛ message_id=%s", new_message_id)

            # Pin the fresh update first, so the group always has a current
            # price at the top. If permissions are missing, leave the post
            # visible and report the required admin permissions.
            pinned = False
            try:
                telegram_api("pinChatMessage", {
                    "chat_id": CHAT_ID,
                    "message_id": new_message_id,
                    "disable_notification": True,
                })
                pinned = True
                logging.info("پیام جدید LAB سنجاق شد")
            except Exception:
                logging.exception("پیام ارسال شد اما سنجاق نشد؛ ربات باید اجازه Pin Messages داشته باشد")

            # Delete only the previous pinned LAB message, never unrelated
            # messages pinned by an administrator.
            if pinned and previous_pinned:
                old_id = previous_pinned.get("message_id")
                old_from = previous_pinned.get("from") or {}
                old_caption = previous_pinned.get("caption") or ""
                is_our_old_lab_post = (
                    old_id
                    and old_from.get("id") == bot_id
                    and "گزارش لحظه‌ای LAB" in old_caption
                )
                if is_our_old_lab_post and old_id != new_message_id:
                    try:
                        telegram_api("deleteMessage", {
                            "chat_id": CHAT_ID,
                            "message_id": old_id,
                        })
                        logging.info("پیام قبلی LAB حذف شد؛ message_id=%s", old_id)
                    except Exception:
                        logging.exception("پیام قبلی LAB حذف نشد؛ ربات باید اجازه Delete Messages داشته باشد")
            return

        except (requests.Timeout, requests.ConnectionError) as error:
            last_error = error
            if attempt < TELEGRAM_MAX_ATTEMPTS:
                time.sleep(TELEGRAM_RETRY_SECONDS * attempt)
                continue
            break

    raise RuntimeError(f"ارسال پیام جدید تلگرام پس از {TELEGRAM_MAX_ATTEMPTS} تلاش ناموفق بود: {last_error}")


def main():
    logging.info("ربات قیمت LAB شروع به کار کرد")
    price, change = get_market_data()

    try:
        history = get_history()
    except Exception:
        logging.exception("تاریخچه دریافت نشد؛ تصویر بدون نمودار تازه ساخته می‌شود")
        history = []

    image_path = create_image(price, change, history)
    send_to_telegram(image_path, make_caption(price, change, history))
    logging.info("قیمت LAB: %.5f | تغییر ۲۴ ساعته: %s", price, "نامشخص" if change is None else f"{change:.2f}%")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logging.exception("اجرای ربات ناموفق بود")
        raise
