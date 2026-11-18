import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "@labcoinss").strip()
WINDOW_SECONDS = 5 * 60
TIMEOUT = (8, 20)
GATE_SPOT_URL = "https://api.gateio.ws/api/v4/spot/trades"
GATE_FUTURES_TRADES_URL = "https://api.gateio.ws/api/v4/futures/usdt/trades"
GATE_FUTURES_CONTRACT_URL = "https://api.gateio.ws/api/v4/futures/usdt/contracts/LAB_USDT"
STATE_FILE = Path(os.getenv("VOLUME_STATE_FILE", ".volume-state/message_id.txt"))
INITIAL_MESSAGE_ID = os.getenv("INITIAL_MESSAGE_ID", "3475").strip()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def dec(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else Decimal(0)
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(0)


def empty_market():
    return {"buy_usd": Decimal(0), "sell_usd": Decimal(0), "buy_count": 0, "sell_count": 0, "trades": 0}


def fetch_spot_trades(now=None):
    """Fetch public Gate.io LAB_USDT spot trades for the last five minutes."""
    now = now or datetime.now(timezone.utc)
    start = int((now - timedelta(seconds=WINDOW_SECONDS)).timestamp())
    end = int(now.timestamp())
    response = requests.get(
        GATE_SPOT_URL,
        params={"currency_pair": "LAB_USDT", "from": start, "to": end, "limit": 1000},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError("Gate.io spot trades response is not a list")
    result = empty_market()
    for trade in payload:
        ts = dec(trade.get("create_time_ms")) / Decimal(1000) if trade.get("create_time_ms") else dec(trade.get("create_time"))
        if ts < start or ts > end:
            continue
        quote = dec(trade.get("price")) * dec(trade.get("amount"))
        side = str(trade.get("side", "")).lower()
        if quote <= 0 or side not in ("buy", "sell"):
            continue
        result["trades"] += 1
        result[side + "_usd"] += quote
        result[side + "_count"] += 1
    return result


def fetch_futures_trades(now=None):
    """Fetch public Gate.io LAB_USDT perpetual trades for the last five minutes."""
    now = now or datetime.now(timezone.utc)
    start_exact = (now - timedelta(seconds=WINDOW_SECONDS)).timestamp()
    end_exact = now.timestamp()
    start = int(start_exact)
    end = int(end_exact) + 1

    contract_response = requests.get(GATE_FUTURES_CONTRACT_URL, timeout=TIMEOUT)
    contract_response.raise_for_status()
    contract = contract_response.json()
    multiplier = dec(contract.get("quanto_multiplier"))
    if multiplier <= 0:
        raise ValueError("Gate.io LAB_USDT futures contract multiplier is missing or invalid")
    logging.info(
        "Gate.io futures contract: name=%s type=%s status=%s multiplier=%s",
        contract.get("name"), contract.get("type"), contract.get("status"), multiplier,
    )
    if contract.get("status") != "trading":
        raise ValueError(f"Gate.io LAB_USDT futures contract is not trading: {contract.get('status')}")

    response = requests.get(
        GATE_FUTURES_TRADES_URL,
        params={"contract": "LAB_USDT", "from": start, "to": end, "limit": 1000},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError("Gate.io futures trades response is not a list")

    logging.info("Gate.io futures returned %s raw trades for LAB_USDT", len(payload))
    result = empty_market()
    for trade in payload:
        # Gate futures create_time_ms is Unix seconds with millisecond decimals
        # (e.g. 1795099200.123), not epoch milliseconds.
        ts = dec(trade.get("create_time_ms")) if trade.get("create_time_ms") is not None else dec(trade.get("create_time"))
        if ts < start_exact or ts > end_exact:
            continue
        size = dec(trade.get("size"))
        quote = dec(trade.get("price")) * abs(size) * multiplier
        if quote <= 0 or size == 0:
            continue
        # Gate public futures trade size: positive is buyer-initiated; negative is seller-initiated.
        side = "buy" if size > 0 else "sell"
        result["trades"] += 1
        result[side + "_usd"] += quote
        result[side + "_count"] += 1
    logging.info(
        "Gate.io futures parsed %s trades in five-minute window (buy=%s sell=%s)",
        result["trades"], result["buy_usd"], result["sell_usd"],
    )
    if len(payload) >= 1000:
        result["possibly_truncated"] = True
    return result


def money(value):
    value = Decimal(value)
    if abs(value) >= Decimal("1000000"):
        return f"${value / Decimal('1000000'):,.2f}M"
    if abs(value) >= Decimal("1000"):
        return f"${value / Decimal('1000'):,.2f}K"
    return f"${value:,.2f}"


def market_section(title, data):
    if data is None:
        return f"{title}\n⚠️ اطلاعات این بازار نرسید؛ فعلاً عددی برای نمایش نداریم."
    total = data["buy_usd"] + data["sell_usd"]
    if data["trades"] == 0:
        return f"{title}\n▫️ تو این ۵ دقیقه معامله‌ای ثبت نشده."
    net = data["buy_usd"] - data["sell_usd"]
    if net > 0:
        result = f"👉 خریدارها {money(net)} بیشتر خرید کردن."
    elif net < 0:
        result = f"👉 فروشنده‌ها {money(abs(net))} بیشتر فروختن."
    else:
        result = "👉 خرید و فروش تقریباً برابر بوده."
    extra = "\n⚠️ تعداد معاملات زیاد بوده؛ ممکنه همه‌شون تو گزارش نیومده باشن." if data.get("possibly_truncated") else ""
    return (
        f"{title}\n"
        f"🟢 خریدها: {money(data['buy_usd'])} | {data['buy_count']} معامله\n"
        f"🔴 فروش‌ها: {money(data['sell_usd'])} | {data['sell_count']} معامله\n"
        f"📊 جمع کل: {money(total)} | {data['trades']} معامله\n"
        f"{result}{extra}"
    )


def build_report(spot, futures, now=None):
    now = now or datetime.now(timezone.utc)
    iran_now = now.astimezone(ZoneInfo("Asia/Tehran"))
    return (
        "🐳 <b>گزارش خرید و فروش LAB</b>\n"
        "⏱ <b>فقط ۵ دقیقه اخیر</b>\n\n"
        + market_section("🟦 <b>اسپات (Spot) | Gate.io</b>", spot)
        + "\n\n"
        + market_section("🟣 <b>فیوچرز (Futures) | Gate.io (قرارداد اهرمی)</b>", futures)
        + f"\n\n🕒 ساعت ایران: {iran_now.strftime('%Y/%m/%d - %H:%M:%S')}\n"
        "ℹ️ خلاصه‌اش: این گزارش می‌گه تو همین ۵ دقیقه کفه خرید سنگین‌تر بوده یا فروش؛ پیش‌بینی قیمت نیست."
    )


def telegram_call(method, payload):
    response = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
        json=payload,
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {data.get('description', 'unknown error')}")
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


def publish_report(text):
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("BOT_TOKEN or CHAT_ID is missing")
    previous_id = load_message_id()

    # Publish the replacement first. If Telegram rejects the new message,
    # keep the previous report visible and retain its ID for the next run.
    sent = telegram_call(
        "sendMessage",
        {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
    )
    message_id = sent.get("message_id")
    if not message_id:
        raise RuntimeError("Telegram sendMessage returned no message_id")
    logging.info("New volume report sent to %s (message_id=%s)", CHAT_ID, message_id)

    # Delete only the previously tracked volume report, never unrelated group messages.
    if previous_id is not None and int(previous_id) != int(message_id):
        try:
            telegram_call("deleteMessage", {"chat_id": CHAT_ID, "message_id": int(previous_id)})
            logging.info("Deleted previous volume report (message_id=%s)", previous_id)
        except Exception as error:
            message = str(error).lower()
            if "message to delete not found" in message or "message_id_invalid" in message:
                logging.info("Previous volume report was already deleted (message_id=%s)", previous_id)
            else:
                logging.exception("Could not delete previous volume report (message_id=%s)", previous_id)

    # Persist the new ID even if deleting the old message failed, so the next run
    # can retry cleanup without ever losing track of the latest report.
    save_message_id(message_id)
    return message_id


def main():
    now = datetime.now(timezone.utc)
    try:
        spot = fetch_spot_trades(now)
    except Exception:
        logging.exception("Spot data unavailable")
        spot = None
    try:
        futures = fetch_futures_trades(now)
    except Exception:
        logging.exception("Futures data unavailable")
        futures = None
    report = build_report(spot, futures, now)
    publish_report(report)


if __name__ == "__main__":
    main()
