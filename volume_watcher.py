import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import requests

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "@lab_chats").strip()
WINDOW_SECONDS = 5 * 60
TIMEOUT = (8, 20)
GATE_SPOT_URL = "https://api.gateio.ws/api/v4/spot/trades"
BINANCE_FUTURES_URL = "https://fapi.binance.com/fapi/v1/aggTrades"

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
    """Fetch public Binance USD-M LABUSDT aggregate trades for the last five minutes."""
    now = now or datetime.now(timezone.utc)
    start_ms = int((now - timedelta(seconds=WINDOW_SECONDS)).timestamp() * 1000)
    end_ms = int(now.timestamp() * 1000)
    result = empty_market()
    from_id = None
    pages = 0
    while pages < 10:
        params = {"symbol": "LABUSDT", "startTime": start_ms, "endTime": end_ms, "limit": 1000}
        if from_id is not None:
            params.pop("startTime", None)
            params["fromId"] = from_id
        response = requests.get(BINANCE_FUTURES_URL, params=params, timeout=TIMEOUT)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("Binance futures trades response is not a list")
        if not payload:
            break
        for trade in payload:
            trade_time = int(trade.get("T", 0))
            if trade_time < start_ms or trade_time > end_ms:
                continue
            quote = dec(trade.get("p")) * dec(trade.get("q"))
            if quote <= 0:
                continue
            # m=true means buyer was maker, so the aggressive side was sell.
            side = "sell" if trade.get("m") is True else "buy"
            result["trades"] += 1
            result[side + "_usd"] += quote
            result[side + "_count"] += 1
        pages += 1
        if len(payload) < 1000:
            break
        next_id = int(payload[-1].get("a", -1)) + 1
        if next_id < 1 or next_id == from_id:
            break
        from_id = next_id
    if pages == 10 and payload and len(payload) == 1000:
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
        return f"{title}\\n⚠️ داده این بازار در این بازه از API دریافت نشد؛ عددی حدس زده نمی‌شود."
    total = data["buy_usd"] + data["sell_usd"]
    if data["trades"] == 0:
        return f"{title}\n▫️ در این بازه معامله‌ای ثبت نشد."
    net = data["buy_usd"] - data["sell_usd"]
    if net > 0:
        pressure = "🟢 برتری خریداران"
    elif net < 0:
        pressure = "🔴 برتری فروشندگان"
    else:
        pressure = "⚪ تعادل نسبی"
    extra = "\n⚠️ احتمالاً بخشی از معاملات به‌دلیل سقف API دریافت نشده." if data.get("possibly_truncated") else ""
    return (
        f"{title}\n"
        f"🟢 حجم خرید: {money(data['buy_usd'])} | {data['buy_count']} معامله\n"
        f"🔴 حجم فروش: {money(data['sell_usd'])} | {data['sell_count']} معامله\n"
        f"📊 حجم کل: {money(total)} | تعداد: {data['trades']}\n"
        f"⚖️ خالص خرید منهای فروش: {money(net)}\n"
        f"{pressure}{extra}"
    )


def build_report(spot, futures, now=None):
    now = now or datetime.now(timezone.utc)
    iran_now = now.astimezone(ZoneInfo("Asia/Tehran"))
    return (
        "🐳 <b>دیده‌بان حجم معاملات LAB</b>\n"
        "⏱ بازه بررسی: ۵ دقیقه اخیر\n\n"
        + market_section("🟦 <b>اسپات | Gate.io – LAB/USDT</b>", spot)
        + "\n\n"
        + market_section("🟣 <b>فیوچرز | Binance USDⓈ-M – LABUSDT</b>", futures)
        + f"\n\n🕒 زمان ایران: {iran_now.strftime('%Y/%m/%d %H:%M:%S')}\n"
        "ℹ️ حجم خرید و فروش بر اساس سمت آغازکننده معامله محاسبه شده؛ این گزارش توصیه مالی نیست."
    )


def send_report(text):
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing")
    if not CHAT_ID:
        raise RuntimeError("CHAT_ID is missing")
    response = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError("Telegram sendMessage failed: " + str(payload.get("description", "unknown error")))
    message_id = (payload.get("result") or {}).get("message_id")
    logging.info("Volume report sent to %s (message_id=%s)", CHAT_ID, message_id)
    return message_id


def main():
    now = datetime.now(timezone.utc)
    errors = []
    try:
        spot = fetch_spot_trades(now)
    except Exception as exc:
        logging.exception("Spot data unavailable")
        spot = None
        errors.append("اسپات: دریافت داده ناموفق")
    try:
        futures = fetch_futures_trades(now)
    except Exception as exc:
        logging.exception("Futures data unavailable")
        futures = None
        errors.append("فیوچرز: دریافت داده ناموفق")
    if spot is None:
        spot = empty_market()
    if futures is None:
        futures = empty_market()
    report = build_report(spot, futures, now)
    if errors:
        report += "\n\n⚠️ " + " | ".join(errors) + "؛ برای جلوگیری از عددسازی، داده ناموجود صفر واقعی تلقی نشود."
    send_report(report)


if __name__ == "__main__":
    main()
