import os
import requests
import jdatetime
from datetime import datetime
from zoneinfo import ZoneInfo

TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ.get("CHAT_ID", "@lab_chats")
MESSAGE_ID = os.environ.get("MESSAGE_ID", "")

API = "https://api.coingecko.com/api/v3/simple/price"

def main():
    data = requests.get(
        API,
        params={
            "ids": "lab",
            "vs_currencies": "usd",
            "include_24hr_change": "true"
        },
        timeout=20
    )
    data.raise_for_status()
    lab = data.json()["lab"]

    price = float(lab["usd"])
    change = float(lab["usd_24h_change"])
    price_text = f"${price:.5f}"

    if change > 0:
        status = "🟢 <b>بازار صعودی</b>"
        movement = f"🟢 📈 <b>+{change:.2f}%</b>"
    elif change < 0:
        status = "🔴 <b>بازار نزولی</b>"
        movement = f"🔴 📉 <b>{change:.2f}%</b>"
    else:
        status = "🟡 <b>بازار بدون تغییر محسوس</b>"
        movement = "🟡 ➖ <b>0.00%</b>"

    now = datetime.now(ZoneInfo("Asia/Tehran"))
    jalali = jdatetime.datetime.fromgregorian(datetime=now)

    message = (
        "✦━━━━━━━━━━━━━━━━━━✦\n"
        "🧪 <b>LAB PREMIUM</b>  <code>LIVE</code>\n"
        "✦━━━━━━━━━━━━━━━━━━✦\n\n"
        "💰 <b>قیمت لحظه‌ای LAB / USD</b>\n"
        f"<b><code>{price_text}</code></b>\n\n"
        f"{status}\n"
        f"📊 <b>تغییرات ۲۴ ساعته:</b> {movement}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🕒 <b>زمان ایران:</b> {jalali.strftime('%Y/%m/%d - %H:%M:%S')}\n"
        "🔄 <b>بروزرسانی:</b> هر ۵ دقیقه\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "👥 <b>گروه رسمی:</b> @lab_chats\n"
        "👨‍💻 <b>طراحی و توسعه:</b> علی حامدی\n"
        "✦━━━━━━━━━━━━━━━━━━✦"
    )

    if MESSAGE_ID:
        method = "editMessageText"
        payload = {
            "chat_id": CHAT_ID,
            "message_id": int(MESSAGE_ID),
            "text": message,
            "parse_mode": "HTML"
        }
    else:
        method = "sendMessage"
        payload = {
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }

    result = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/{method}",
        json=payload,
        timeout=20
    )
    result.raise_for_status()
    response = result.json()

    if not response.get("ok"):
        raise RuntimeError(response)

    if not MESSAGE_ID:
        print("MESSAGE_ID =", response["result"]["message_id"])

    print("LAB price updated successfully:", price_text)


if __name__ == "__main__":
    main()
