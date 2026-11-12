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
PLATFORM = os.getenv("CMC_PLATFORM", "bsc")
CMC_URL = "https://pro-api.coinmarketcap.com/public-api/v1/dex/holders/list"
STATE_FILE = Path("top_holders.json")
OUTPUT_FILE = Path("lab_whale_watch.jpg")
STATUS_OUTPUT_FILE = Path("lab_whale_status.jpg")
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
    # The holders/list endpoint expects a platform name/slug (for example
    # "ethereum"), not a numeric platform ID. Try the BNB slug first and
    # retain explicit fallbacks for CMC's alternate platform representations.
    configured = PLATFORM.strip()
    candidates = []
    for candidate in (configured, "bsc", "BNB Smart Chain", "bnb-smart-chain", "14"):
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    payload = None
    failures = []
    for platform in candidates:
        response = requests.post(
            CMC_URL,
            json={"tokenAddress": CONTRACT, "platform": platform, "tag": "tag_all"},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=TIMEOUT,
        )
        if not response.ok:
            detail = response.text[:1200].replace("\\n", " ")
            failures.append(f"{platform}: HTTP {response.status_code} {detail}")
            # Only retry a parameter-format rejection. Other HTTP errors
            # (rate limits, server failures, auth) should fail immediately.
            if response.status_code == 400 and "Parameter error" in response.text:
                continue
            raise RuntimeError(f"CoinMarketCap request failed for platform {platform}: {failures[-1]}")
        candidate_payload = response.json()
        status = candidate_payload.get("status") or {}
        error_code = status.get("error_code")
        # CMC sometimes serializes the success code as the string "0".
        if error_code not in (None, 0, "0"):
            error = f"{platform}: CMC {error_code} {status.get('error_message')}"
            failures.append(error)
            if str(error_code) in {"400", "1001"}:
                continue
            raise RuntimeError(error)
        payload = candidate_payload
        logging.info("CoinMarketCap platform accepted: %s", platform)
        break

    if payload is None:
        raise RuntimeError("CoinMarketCap rejected all BNB platform formats: " + " | ".join(failures))

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
        return raw if isinstance(raw, dict) else {}
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
        magnitudes = [abs(item["delta"]) for item in changes if item["delta"] is not None and item["delta"] != 0]
        max_magnitude = max(magnitudes, default=Decimal("1"))
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
                # Mini bar compares this wallet's balance change with the largest change in the report.
                bar_width = max(4, int((abs(item["delta"]) / max_magnitude) * 260))
                draw.rounded_rectangle((410, y + 78, 675, y + 91), radius=6, fill="#26384D")
                draw.rounded_rectangle((410, y + 78, 410 + bar_width, y + 91), radius=6, fill=accent)
                if abs(item["delta"]) >= ALERT_THRESHOLD:
                    draw.text((width - 95, y + 105), "هشدار تغییر بزرگ", font=get_font(18, True), fill="#F4C66A", anchor="rs")
            y += 118

    draw.line((75, height - 160, width - 75, height - 160), fill="#2A3B52", width=2)
    rtl_text(draw, (width - 90, height - 112), "تغییر موجودی به‌تنهایی به معنی خرید یا فروش نیست", get_font(24), "#A9BED8")
    draw.text((90, height - 112), "منبع داده: CoinMarketCap", font=get_font(18), fill="#7189A5", anchor="ls")
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


def create_status_graphic(changes, first_run=False):
    width, height = 1200, 900
    image = Image.new("RGB", (width, height), "#07111F")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((24, 24, width - 24, height - 24), radius=42, fill="#101D30", outline="#29415F", width=3)
    draw.rounded_rectangle((55, 55, width - 55, 250), radius=32, fill="#172B45")
    rtl_text(draw, (width - 90, 120), "دیده‌بان نهنگ‌های LAB", get_font(62, True), "#F5F8FF")
    rtl_text(draw, (width - 90, 195), "گزارش زنده ۱۰ کیف پول برتر", get_font(32), "#AFC4DE")
    date_text, time_text = iran_time()
    draw.text((90, 215), f"{fa_digits(date_text)}  |  {fa_digits(time_text)}", font=get_font(24), fill="#AFC4DE", anchor="ls")

    balance_changes = sum(1 for item in changes if item["delta"] is not None and item["delta"] != 0)
    rank_changes = sum(1 for item in changes if item["old_rank"] != item["new_rank"])
    if first_run:
        headline, detail, accent = "نقطه شروع ثبت شد", "از بررسی بعدی، تغییرات با این آمار مقایسه می‌شوند", "#F4C66A"
    elif not changes:
        headline, detail, accent = "فعلاً تغییری ثبت نشده", "موجودی و رتبه ۱۰ کیف پول برتر ثابت مانده است", "#29E69A"
    else:
        headline, detail, accent = "تغییر در کیف پول‌ها شناسایی شد", "جزئیات تغییرات در پیام هشدار جداگانه ارسال شده است", "#F4C66A"

    draw.rounded_rectangle((70, 300, width - 70, 500), radius=30, fill="#14283B", outline=accent, width=3)
    rtl_text(draw, (width - 105, 375), headline, get_font(43, True), accent)
    rtl_text(draw, (width - 105, 445), detail, get_font(26), "#D5E2F1")

    cards = [
        (75, 550, 555, 730, "#17352F", "#29E69A", fa_digits(balance_changes), "تغییر موجودی"),
        (645, 550, 1125, 730, "#302A20", "#F4C66A", fa_digits(rank_changes), "تغییر رتبه"),
    ]
    for x1, y1, x2, y2, panel, accent_color, number, label in cards:
        draw.rounded_rectangle((x1, y1, x2, y2), radius=28, fill=panel, outline=accent_color, width=2)
        draw.text(((x1 + x2) // 2, y1 + 70), number, font=get_font(58, True), fill=accent_color, anchor="mm")
        rtl_text(draw, ((x1 + x2) // 2, y1 + 140), label, get_font(28, True), "#E7EEF8", anchor="mm")

    draw.line((80, 785, width - 80, 785), fill="#2A3D55", width=2)
    rtl_text(draw, (width - 90, 830), "هر ۵ دقیقه یک‌بار بررسی می‌شود", get_font(25), "#AFC4DE")
    draw.text((90, 830), "BSC  •  LAB", font=get_font(22, True), fill="#29E69A", anchor="ls")
    image.save(STATUS_OUTPUT_FILE, "JPEG", quality=95, optimize=True)
    return STATUS_OUTPUT_FILE


def send_status(changes, previous_message_id=None, first_run=False):
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN تنظیم نشده است")
    date_text, time_text = iran_time()
    balance_changes = sum(1 for item in changes if item["delta"] is not None and item["delta"] != 0)
    rank_changes = sum(1 for item in changes if item["old_rank"] != item["new_rank"])
    if first_run:
        status_line = "نقطه شروع ثبت شد؛ از بررسی بعدی تغییرات قابل مقایسه‌اند."
    elif not changes:
        status_line = "هیچ تغییری در موجودی یا رتبه ۱۰ کیف پول برتر ثبت نشد."
    else:
        status_line = f"{fa_digits(balance_changes)} تغییر موجودی و {fa_digits(rank_changes)} تغییر رتبه شناسایی شد."
    caption = (
        "<b>🐳 وضعیت دوره‌ای دیده‌بان LAB</b>\n"
        f"📅 {date_text} | 🕒 {time_text} (ایران)\n"
        f"🔎 {status_line}\n"
        "ℹ️ تغییر موجودی لزوماً به معنی خرید یا فروش نیست.\n"
        "👥 @lab_chats"
    )
    image_path = create_status_graphic(changes, first_run=first_run)
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
        raise RuntimeError(f"Telegram status sendPhoto failed: {result.get('description')}")
    new_message_id = result["result"]["message_id"]

    # Delete only the previous periodic status post; separate whale alerts remain in the group.
    if previous_message_id:
        delete_url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage"
        try:
            deleted = requests.post(
                delete_url,
                data={"chat_id": CHAT_ID, "message_id": previous_message_id},
                timeout=TIMEOUT,
            )
            deleted.raise_for_status()
            if not deleted.json().get("ok"):
                logging.warning("پیام وضعیت قبلی حذف نشد: %s", deleted.text[:300])
        except requests.RequestException as exc:
            logging.warning("حذف پیام وضعیت قبلی ناموفق بود: %s", exc)
    return new_message_id

def save_state(holders, status_message_id):
    payload = {
        "holders": {item["address"]: item["balance"] for item in holders},
        "status_message_id": status_message_id,
        "updated_at": datetime.now(ZoneInfo("UTC")).isoformat(),
    }
    STATE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    current = fetch_holders()
    state = load_state()
    previous = state.get("holders", {})
    first_run = not previous

    changes = compare(previous, current) if previous else []
    if changes:
        image_path = create_graphic(changes)
        send_to_telegram(image_path, changes)

    new_status_id = send_status(
        changes,
        previous_message_id=state.get("status_message_id"),
        first_run=first_run,
    )
    save_state(current, new_status_id)
    if first_run:
        logging.info("نقطه شروع ۱۰ هولدر ثبت شد؛ گزارش وضعیت اولیه در گروه ارسال شد")
    elif not changes:
        logging.info("بدون تغییر؛ فقط پیام وضعیت جدید جایگزین پیام وضعیت قبلی شد")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logging.exception("دیده‌بان کیف پول‌ها اجرا نشد")
        sys.exit(1)
