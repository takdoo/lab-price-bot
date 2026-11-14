"""Free public-API market analysis for LAB/USDT."""
import logging, os
from decimal import Decimal, InvalidOperation
import requests

TIMEOUT = (8, 20)
BINANCE = "https://api.binance.com"
BYBIT = "https://api.bybit.com"

def dec(value):
    try:
        n = Decimal(str(value))
        return n if n.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None

def get(url, params):
    r = requests.get(url, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()

def fetch_spot_markets():
    markets = []
    try:
        row = get(BINANCE + "/api/v3/ticker/24hr", {"symbol": "LABUSDT"})
        p, v, c = dec(row.get("lastPrice")), dec(row.get("quoteVolume")), dec(row.get("priceChangePercent"))
        if p and p > 0:
            markets.append({"exchange":"Binance","price":p,"volume":v or Decimal(0),"change":c})
    except (requests.RequestException, ValueError, TypeError, AttributeError, RuntimeError) as e:
        logging.info("Binance spot unavailable: %s", e)
    try:
        payload = get(BYBIT + "/v5/market/tickers", {"category":"spot","symbol":"LABUSDT"})
        if str(payload.get("retCode")) != "0":
            raise RuntimeError(str(payload.get("retMsg")))
        rows = (payload.get("result") or {}).get("list") or []
        if rows:
            row = rows[0]
            p, v, c = dec(row.get("lastPrice")), dec(row.get("turnover24h")), dec(row.get("price24hPcnt"))
            if p and p > 0:
                markets.append({"exchange":"Bybit","price":p,"volume":v or Decimal(0),"change":c*100 if c is not None else None})
    except (requests.RequestException, ValueError, TypeError, AttributeError, RuntimeError) as e:
        logging.info("Bybit spot unavailable: %s", e)
    return markets

def volume_anomaly():
    try:
        rows = get(BINANCE + "/api/v3/klines", {"symbol":"LABUSDT","interval":"1d","limit":9})
        completed = rows[:-1] if len(rows) >= 9 else rows
        if len(completed) < 8:
            return None
        latest = dec(completed[-1][7])
        prior = [dec(x[7]) for x in completed[-8:-1]]
        if latest is None or any(x is None for x in prior):
            return None
        avg = sum(prior, Decimal(0))/Decimal(len(prior))
        if avg <= 0:
            return None
        ratio = latest/avg
        return {"latest":latest,"average":avg,"ratio":ratio,"spike":ratio >= Decimal("2")}
    except (requests.RequestException, ValueError, TypeError, IndexError, KeyError):
        logging.exception("Volume history unavailable")
        return None

def support_resistance():
    try:
        rows = get(BINANCE + "/api/v3/klines", {"symbol":"LABUSDT","interval":"1h","limit":168})
        highs = [dec(x[2]) for x in rows if len(x)>2]
        lows = [dec(x[3]) for x in rows if len(x)>3]
        highs, lows = [x for x in highs if x is not None], [x for x in lows if x is not None]
        if not highs or not lows:
            return None
        return {"resistance":max(highs),"support":min(lows)}
    except (requests.RequestException, ValueError, TypeError, IndexError):
        logging.exception("Candle history unavailable")
        return None

def build_report(markets, volume, levels):
    lines = ["<b>📈 گزارش روزانه بازار LAB</b>"]
    if markets:
        lines.append("<b>قیمت اسپات LAB/USDT</b>")
        for m in markets:
            c = f"{m['change']:+.2f}%" if m["change"] is not None else "نامشخص"
            lines.append(f"• {m['exchange']}: <code>{m['price']:.8f}</code> USDT | تغییر ۲۴ساعته: {c} | حجم: {m['volume']:,.0f} USDT")
        prices=[m["price"] for m in markets]
        if len(prices)>1:
            spread=(max(prices)-min(prices))/min(prices)*100
            lines.append(f"اختلاف قیمت صرافی‌ها: {spread:.2f}%")
    else:
        lines.append("قیمت اسپات: نماد LAB/USDT در صرافی‌های بررسی‌شده در دسترس نبود.")
    if volume:
        lines.append(f"حجم روز کامل اخیر: {volume['latest']:,.0f} USDT | میانگین ۷ روز قبل: {volume['average']:,.0f} | نسبت: {volume['ratio']:.2f}x")
        if volume["spike"]:
            lines.append("⚠️ جهش حجم: حجم روز کامل اخیر حداقل ۲ برابر میانگین ۷ روز قبل بوده است.")
    else:
        lines.append("جهش حجم: داده تاریخی کافی/در دسترس نیست.")
    if levels:
        lines.append(f"بازه ۷ روزه: حمایت محاسباتی <code>{levels['support']:.8f}</code> | مقاومت محاسباتی <code>{levels['resistance']:.8f}</code>")
        lines.append("این سطوح صرفاً کمینه/بیشینه بازه‌اند و توصیه معاملاتی نیستند.")
    else:
        lines.append("حمایت/مقاومت: داده کندل در دسترس نبود.")
    if markets:
        changes=[m["change"] for m in markets if m["change"] is not None]
        if changes:
            avg=sum(changes,Decimal(0))/Decimal(len(changes))
            mood="مثبت" if avg>1 else "منفی" if avg < -1 else "خنثی/کم‌نوسان"
            lines.append(f"وضعیت ساده بازار بر اساس تغییر ۲۴ساعته: {mood} ({avg:+.2f}%).")
    lines.append("ℹ️ تحلیل خودکار داده‌های عمومی است، نه سیگنال خرید یا فروش.")
    return "\\n".join(lines)

def send_report(report):
    token=os.getenv("BOT_TOKEN","").strip()
    chat=os.getenv("CHAT_ID","@lab_chats").strip()
    if not token or not chat:
        raise RuntimeError("BOT_TOKEN یا CHAT_ID تنظیم نشده است.")
    r=requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
        data={"chat_id":chat,"text":report,"parse_mode":"HTML","disable_web_page_preview":True},timeout=TIMEOUT)
    r.raise_for_status()
    result=r.json()
    if not result.get("ok"):
        raise RuntimeError(str(result.get("description")))
    return result["result"]["message_id"]

def main():
    report=build_report(fetch_spot_markets(),volume_anomaly(),support_resistance())
    print(report)
    if os.getenv("SEND_TELEGRAM_REPORT","false").lower()=="true":
        send_report(report)

if __name__=="__main__":
    main()
