import json
import logging
import os
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

import jdatetime
import requests
from PIL import Image, ImageDraw, ImageFont

CONTRACT = "0x7ec43cf65f1663f820427c62a5780b8f2e25593a"
PLATFORM = os.getenv("CMC_PLATFORM", "BNB Smart Chain")
CMC_URL = "https://pro-api.coinmarketcap.com/public-api/v1/dex/holders/list"
STATE_FILE = Path("top_holders.json")
OUTPUT_FILE = Path("lab_whale_watch.jpg")
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "@lab_chats").strip()
ALERT_THRESHOLD = Decimal(os.getenv("WHALE_ALERT_LAB", "1000000"))
TIMEOUT = (10, 30)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def fa_digits(value):
    return str(value).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def fmt_amount(value):
    number = Decimal(str(value))
    if abs(number) >= Decimal("1000000"):
        text = f"{number / Decimal('1000000'):,.2f} میلیون"
    elif abs(number) >= Decimal("1000"):
        text = f"{number:,.0f}"
    else:
        text = f"{number:,.2f}"
    return fa_digits(text)


def iran_time():
    now = datetime.now(ZoneInfo("Asia/Tehran"))
    jalali = jdatetime.datetime.fromgregorian(datetime=now)
    return jalali.strftime("%Y/%m/%d"), now.strftime("%H:%M")


def fetch_holders():
    response = requests.post(
        CMC_URL,
        json={"tokenAddress": CONTRACT, "platform": PLATFORM, "tag": "tag_all"},
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    status = payload.get("status") or {}
    if status.get("error_code") not in (None, 0):
        raise RuntimeError(f"CoinMarketCap error {status.get('error_code')}: {status.get('error_message')}")
    data = payload.get("data") or {}
    rows = data.get("holders") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError("پاسخ فهرست هولدرها ساختار معتبر ندارد")
    holders = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        address = str(row.get("walletAddress") or "").strip().lower()
        raw_balance = row.get("actualBalance") or row.get("balance")
        if not address.startswith("0x") or len(address) != 42 or raw_balance is None:
            continue
        try:
            balance = Decimal(str(raw_balance))
        except InvalidOperation:
            continue
        if not balance.is_finite() or balance < 0 or address in seen:
            continue
        seen.add(address)
        holders.append({"address": address, "balance": str(balance)})
    holders.sort(key=lambda item: Decimal(item["balance"]), reverse=True)
    if len(holders) < 10:
        raise ValueError(f"فقط {len(holders)} کیف پول معتبر دریافت شد؛ برای گزارش ۱۰ هولدر کافی نیست")
    return holders[:10]


def load_state():
    if not STATE_FILE.exists():
        return {}
    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return raw.get("holders", {}) if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"فایل وضعیت قبلی خراب یا غیرقابل‌خواندن است: {exc}") from exc


def compare(old, new):
    changes = []
    old_rank = {address: rank for rank, address in enumerate(old, start=1)}
    new_rank = {item["address"]: rank for rank, item in enumerate(new, start=1)}
    all_addresses = set(old) | set(new_rank)
    for address in all_addresses:
        before = old.get(address)
        after_item = next((item for item in new if item["address"] == address), None)
        after = after_item["balance"] if after_item else None
        previous_rank = old_rank.get(address)
        current_rank = new_rank.get(address)
        delta = None
        if before is not None and after is not None:
            delta = Decimal(after) - Decimal(str(before))
        balance_changed = delta is not None and delta != 0
        rank_changed = previous_rank != current_rank
        if balance_changed or rank_changed:
            changes.append({
                "address": address,
                "before": before,
                "after": after,
                "delta": delta,
                "old_rank": previous_rank,
                "new_rank": current_rank,
                "entered": previous_rank is None and current_rank is not None,
                "left": previous_rank is not None and current_rank is None,
            })
    changes.sort(key=lambda item: (
        0 if item["delta"] is not None and item["delta"] != 0 else 1,
        -abs(item["delta"]) if item["delta"] is not None else Decimal(0),
        item["new_rank"] or 99,
    ))
    return changes


def get_font(size, bold=False):
    candidates = [
        "/usr/share/fonts/truetype/vazirmatn/Vazirmatn-Bold.ttf" if bold else "/usr/share/fonts/truetype/vazirmatn/Vazirmatn-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf" if bold else "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def rtl_text(draw, xy, text, font, fill, anchor="ra"):
    try:
        draw.text(xy, text, font=font, fill=fill, anchor=anchor, direction="rtl", language="fa")
    except (TypeError, ValueError):
        draw.text(xy, text, font=font, fill=fill, anchor=anchor)


def create_graphic(changes):
    width, height = 1200, 1700
    image = Image.new("RGB", (width, height), "#0B1220")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((28, 28, width - 28, height - 28), radius=38, fill="#111C2E", outline="#263B56", width=3)
    draw.rounded_rectangle((55, 55, width - 55, 250), radius=30, fill="#172840")
    rtl_text(draw, (width - 90, 105), "دیده‌بان نهنگ‌های LAB", get_font(58, True), "#F3F7FF")
    rtl_text(draw, (width - 90, 180), "گزارش تغییر موجودی ۱۰ کیف پول برتر", get_font(30), "#A9BED8")
    date_text, time_text = iran_time()
    draw.text((90, 215), f"{fa_digits(date_text)}  |  {fa_digits(time_text)}", font=get_font(23), fill="#90A9C5", anchor="ls")

    if not changes:
        rtl_text(draw, (width - 90, 380), "تغییر تازه‌ای ثبت نشده است", get_font(40, True), "#DCE8F7")
    else:
        y = 285
        for item in changes[:10]:
            positive = item["delta"] is not None and item["delta"] > 0
            negative = item["delta"] is not None and item["delta"] < 0
            accent = "#29E69A" if positive else "#FF6378" if negative else "#F4C66A"
            panel = "#142C31" if positive else "#321F2B" if negative else "#302B20"
            draw.rounded_rectangle((65, y, width - 65, y + 108), radius=22, fill=panel, outline=accent, width=2)
            rank = item["new_rank"] or item["old_rank"] or 0
            title = f"کیف پول رتبه {fa_digits(rank)}"
            if item["entered"]:
                state = "ورود به ۱۰ کیف پول برتر"
            elif item["left"]:
                state = "خروج از ۱۰ کیف پول برتر"
            elif positive:
                state = "افزایش موجودی"
            elif negative:
                state = "کاهش موجودی"
            else:
                state = f"تغییر رتبه: {fa_digits(item['old_rank'])} ← {fa_digits(item['new_rank'])}"
            rtl_text(draw, (width - 95, y + 38), title, get_font(28, True), "#F4F7FC")
            rtl_text(draw, (width - 95, y + 82), state, get_font(23), accent)
            short = item["address"][:8] + "…" + item["address"][-6:]
            draw.text((95, y + 38), short, font=get_font(20), fill="#9EB3CC", anchor="ls")
            if item["delta"] is not None and item["delta"] != 0:
                sign = "+" if positive else "−"
                delta_text = sign + fmt_amount(abs(item["delta"])) + " LAB"
                draw.text((95, y + 82), delta_text, font=get_font(23, True), fill=accent, anchor="ls")
                if abs(item["delta"]) >= ALERT_THRESHOLD:
                    draw.text((width - 95, y + 105), "هشدار تغییر بزرگ", font=get_font(18, True), fill="#F4C66A", anchor="rs")
            y += 118

    draw.line((75, height - 160, width - 75, height - 160), fill="#2A3B52", width=2)
    rtl_text(draw, (width - 90, height - 112), "تغییر موجودی به‌تنهایی به معنی خرید یا فروش نیست", get_font(24), "#A9BED8")
    draw.text((90, height - 58), "BSC  •  LAB", font=get_font(23, True), fill="#29E69A", anchor="ls")
    rtl_text(draw, (width - 90, height - 58), "گروه تلگرامی @lab_chats", get_font(23), "#DCE8F7")
    image.save(OUTPUT_FILE, "JPEG", quality=94, optimize=True)
    return OUTPUT_FILE


def send_to_telegram(image_path, changes):
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN تنظیم نشده است")
    date_text, time_text = iran_time()
    caption = (
        "<b>🐳 دیده‌بان نهنگ‌های LAB</b>\n"
        f"📅 {date_text} | 🕒 {time_text} (ایران)\n"
        f"🔎 تعداد تغییرات شناسایی‌شده: <b>{len(changes)}</b>\n"
        "ℹ️ تغییر موجودی الزاماً به معنی خرید یا فروش نیست.\n"
        "👥 @lab_chats"
    )
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    with open(image_path, "rb") as photo:
        response = requests.post(
            url,
            data={"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"},
            files={"photo": photo},
            timeout=TIMEOUT,
        )
    response.raise_for_status()
    result = response.json()
    if not result.get("ok"):
        raise RuntimeError(f"Telegram sendPhoto failed: {result.get('description')}")
    logging.info("گزارش تغییر کیف پول‌ها در گروه ارسال شد")


def save_state(holders):
    payload = {
        "holders": {item["address"]: item["balance"] for item in holders},
        "updated_at": datetime.now(ZoneInfo("UTC")).isoformat(),
    }
    STATE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    current = fetch_holders()
    previous = load_state()
    if not previous:
        save_state(current)
        logging.info("اولین تصویر مبنا ذخیره شد؛ برای جلوگیری از هشدار اشتباه، پیامی ارسال نشد")
        return

    changes = compare(previous, current)
    if changes:
        image_path = create_graphic(changes)
        send_to_telegram(image_path, changes)
    else:
        logging.info("هیچ تغییر واقعی در موجودی یا رتبه ۱۰ هولدر برتر دیده نشد؛ پیامی ارسال نمی‌شود")
    if changes:
        save_state(current)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logging.exception("دیده‌بان کیف پول‌ها اجرا نشد")
        sys.exit(1)
