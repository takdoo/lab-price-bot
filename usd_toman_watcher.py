import logging
import os
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

API_URL = "https://nerkhara.com/api/v1/latest/usd.json"
API_SITE = "https://nerkhara.com/"
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "@labcoinss").strip()
STATE_FILE = Path(os.getenv("USD_STATE_FILE", ".usd-state/message_id.txt"))
INITIAL_MESSAGE_ID = os.getenv("INITIAL_MESSAGE_ID", "").strip()
TIMEOUT = (10, 25)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def decimal_value(value, field):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"Invalid {field} in USD rate response")
    if not number.is_finite() or number <= 0:
        raise ValueError(f"Invalid {field} in USD rate response")
    return number


def fetch_usd_rate():
    response = requests.get(
        API_URL,
        headers={"Accept": "application/json", "User-Agent": "LAB-USD-Toman-Watcher/1.0"},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("USD API returned an unexpected response")

    item = payload.get("data") or payload.get("item") or payload
    if isinstance(item, list):
        item = next((row for row in item if isinstance(row, dict) and row.get("id") == "usd"), None)
    if not isinstance(item, dict):
        raise ValueError("USD rate item is missing")

    price = decimal_value(item.get("price"), "price")
    unit = str(item.get("unit") or item.get("currency") or "").upper()
    if unit not in {"IRT", "TOMAN", "TOMANS"}:
        raise ValueError(f"USD API unit is not toman (received {unit or 'missing'})")

    stale = bool(item.get("stale", False))
    updated = item.get("updated_utc") or item.get("checked_at") or item.get("timestamp_tehran") or item.get("timestamp")
    if not updated:
        raise ValueError("USD API did not provide a quote timestamp")
    change = item.get("change_pct")
    try:
        change = Decimal(str(change)) if change is not None else None
        if change is not None and not change.is_finite():
            change = None
    except (InvalidOperation, ValueError):
        change = None

    sources = item.get("sources")
    if isinstance(sources, list):
        source_count = len(sources)
    elif sources is None:
        source_count = None
    else:
        source_count = 1
    return {"price": price, "updated": str(updated), "stale": stale, "change_pct": change,
            "source_count": source_count, "source": str(item.get("method") or "NerkhAra")}


def format_toman(value):
    return f"{value:,.0f}".translate(str.maketrans("0123456789,", "۰۱۲۳۴۵۶۷۸۹٬"))


def build_report(rate, now=None):
    now = now or datetime.now(timezone.utc)
    iran_now = now.astimezone(ZoneInfo("Asia/Tehran"))
    price = format_toman(rate["price"])
    if rate["change_pct"] is None:
        change_line = ""
    else:
        change = rate["change_pct"]
        sign = "+" if change > 0 else ""
        change_line = f"\n📈 تغییر اعلام‌شده: {sign}{change}%"
    source_line = (
        f"\n🔎 تعداد منابع: {rate['source_count']}"
        if rate["source_count"] is not None else ""
    )
    freshness = "\n⚠️ هشدار: دادهٔ منبع قدیمی علامت‌گذاری شده است." if rate["stale"] else ""
    return (
        "💵 <b>نرخ دلار آزاد به تومان</b>\n"
        f"🇺🇸 هر ۱ دلار: <b>{price} تومان</b>"
        + change_line
        + f"\n🕒 زمان آخرین به‌روزرسانی منبع: {rate['updated']}"
        + f"\n⏱ زمان ارسال (ایران): {iran_now.strftime('%Y/%m/%d - %H:%M:%S')}"
        + source_line
        + freshness
        + f"\n🌐 منبع: <a href=\"{API_SITE}\">NerkhAra</a>"
    )


def telegram_call(method, payload):
    response = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
        json=payload,
        timeout=TIMEOUT,
    )
    try:
        data = response.json()
    except ValueError:
        response.raise_for_status()
        raise RuntimeError(f"Telegram {method} returned non-JSON")
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {data.get('description', 'unknown error')}")
    response.raise_for_status()
    return data.get("result") or {}


def load_message_id():
    try:
        value = STATE_FILE.read_text(encoding="utf-8").strip()
        return int(value) if value else None
    except (FileNotFoundError, ValueError, OSError):
        return int(INITIAL_MESSAGE_ID) if INITIAL_MESSAGE_ID.isdigit() else None


def save_message_id(message_id):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(str(message_id), encoding="utf-8")


def publish_report(report):
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("BOT_TOKEN or CHAT_ID is missing")
    previous_id = load_message_id()
    sent = telegram_call("sendMessage", {
        "chat_id": CHAT_ID,
        "text": report,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    })
    message_id = sent.get("message_id")
    if not message_id:
        raise RuntimeError("Telegram did not return a message_id")
    logging.info("USD/Toman update sent; message_id=%s", message_id)

    if previous_id is not None and int(previous_id) != int(message_id):
        try:
            telegram_call("deleteMessage", {"chat_id": CHAT_ID, "message_id": int(previous_id)})
            logging.info("Previous USD/Toman message deleted: %s", previous_id)
        except Exception as exc:
            logging.warning("Could not delete previous USD/Toman message: %s", exc)
    save_message_id(message_id)
    return message_id


def main():
    rate = fetch_usd_rate()
    report = build_report(rate)
    publish_report(report)


if __name__ == "__main__":
    main()
