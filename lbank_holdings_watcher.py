import logging
import os
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from html import unescape
from pathlib import Path
from zoneinfo import ZoneInfo

import jdatetime
import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont

URL = "https://www.lbank.com/price/lab/funds-holdings"
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "@labcoinss").strip()
STATE_FILE = Path(os.getenv("LBANK_STATE_FILE", ".lbank-state/message_id.txt"))
OUT = Path("lab_lbank_holdings.jpg")
TIMEOUT = (15, 45)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def dec(value):
    text = str(value or "").replace(",", "").replace("−", "-").strip()
    match = re.search(r"[-+]?\\d+(?:\\.\\d+)?\\s*[KMB]?", text, re.I)
    if not match:
        raise ValueError(f"Invalid LBank numeric value: {value!r}")
    token = match.group(0).replace(" ", "")
    suffix = token[-1].upper() if token[-1].isalpha() else ""
    number = Decimal(token[:-1] if suffix else token)
    return number * {"K": Decimal(1000), "M": Decimal(1000000), "B": Decimal(1000000000), "": Decimal(1)}[suffix]


def parse_rows(html):
    soup = BeautifulSoup(html, "html.parser")
    wanted = ("holding address", "holding amount", "1-day position change", "7-day position change")
    for table in soup.find_all("table"):
        headers = [re.sub(r"\\s+", " ", cell.get_text(" ", strip=True)).lower() for cell in table.find_all("th")]
        if not all(any(term in h for h in headers) for term in wanted):
            continue
        indexes = {term: next(i for i, h in enumerate(headers) if term in h) for term in wanted}
        rows = []
        for tr in table.find_all("tr"):
            cells = [cell.get_text(" ", strip=True) for cell in tr.find_all(["td", "th"])]
            if len(cells) <= max(indexes.values()) or cells == headers:
                continue
            try:
                address = cells[indexes["holding address"]].strip()
                balance = dec(cells[indexes["holding amount"]])
                day = dec(cells[indexes["1-day position change"]])
                week = dec(cells[indexes["7-day position change"]])
            except (ValueError, InvalidOperation, IndexError):
                continue
            if not address or address.lower() in {"holding address", "--"}:
                continue
            rows.append({"address": address, "balance": balance, "day": day, "week": week})
        if rows:
            return rows[:10]
    # LBank renders the holdings grid as divs rather than a semantic HTML table.
    text = soup.get_text(" ", strip=True)
    marker = "Token holdings of top 50 addresses"
    if marker not in text:
        raise RuntimeError("LBank holdings section is missing from the returned page")
    section = text.split(marker, 1)[1]
    pattern = re.compile(
        r"(?:[0-9]+[ ]+)?(0x[a-f0-9]{4}[.]{3}[a-f0-9]{4,})[ ]+"
        r"([0-9,.]+[ ]*[KMB]?)[ ]+([0-9.]+%)[ ]+"
        r"([-+−]?[0-9,.]+[ ]*[KMB]?)[ ]+([-+−]?[0-9,.]+[ ]*[KMB]?)",
        re.I,
    )
    for match in pattern.finditer(section):
        try:
            rows.append({"address": match.group(1), "balance": dec(match.group(2)),
                         "day": dec(match.group(4)), "week": dec(match.group(5))})
        except (ValueError, InvalidOperation):
            continue
    if rows:
        return rows[:10]
    raise RuntimeError("LBank holdings section found, but row format was not recognized: " + section[:900])


def fetch_rows():
    response = requests.get(URL, headers={"User-Agent":"Mozilla/5.0 (compatible; LAB-LBank-Watcher/1.0)", "Accept-Language":"en-US,en;q=0.9"}, timeout=TIMEOUT)
    response.raise_for_status()
    rows = parse_rows(response.text)
    if len(rows) < 10:
        raise RuntimeError(f"LBank returned only {len(rows)} usable top-holder rows")
    logging.info("Fetched %d LAB holders from LBank", len(rows))
    return rows


def short(value):
    value = str(value)
    if len(value) > 22:
        return value[:9] + "…" + value[-7:]
    return value


def amount(value):
    sign = "+" if value > 0 else "−" if value < 0 else ""
    n = abs(value)
    if n >= Decimal(1000000):
        return f"{sign}{n/Decimal(1000000):,.2f}M"
    if n >= Decimal(1000):
        return f"{sign}{n/Decimal(1000):,.2f}K"
    return f"{sign}{n:,.2f}"


def fa(value):
    return str(value).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def font(size, bold=False):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf" if bold else "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf"
    ]
    for p in paths:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size, layout_engine=ImageFont.Layout.RAQM)
            except Exception:
                return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def rtl(draw, xy, text, fnt, fill, anchor="ra"):
    try:
        draw.text(xy, text, font=fnt, fill=fill, anchor=anchor, direction="rtl", language="fa")
    except (TypeError, ValueError):
        import arabic_reshaper
        from bidi.algorithm import get_display
        draw.text(xy, get_display(arabic_reshaper.reshape(text)), font=fnt, fill=fill, anchor=anchor)


def make_image(rows):
    width, height = 1500, 1180
    im = Image.new("RGB", (width, height), "#070B13")
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((24,24,width-24,height-24), radius=34, fill="#101B2B", outline="#29415D", width=3)
    d.rounded_rectangle((55,55,width-55,205), radius=28, fill="#172B43")
    rtl(d,(width-95,112),"دیده‌بان موجودی نهنگ‌های LAB",font(48,True),"#F4F7FC")
    rtl(d,(width-95,170),"منبع: LBank Token Holdings | شبکه BNB Smart Chain",font(23),"#AFC2D9")
    now=datetime.now(ZoneInfo("Asia/Tehran"))
    jalali=jdatetime.datetime.fromgregorian(datetime=now)
    d.text((90,155),fa(jalali.strftime("%Y/%m/%d"))+"  "+fa(now.strftime("%H:%M")),font=font(22),fill="#AFC2D9")
    # Table headers
    y0=250
    headers=[("رتبه",1390),("آدرس کیف پول",1110),("موجودی",760),("تغییر ۲۴ساعته",470),("تغییر ۷روزه",185)]
    for label,x in headers:
        rtl(d,(x,y0),label,font(24,True),"#D9E6F5")
    d.line((70,285,width-70,285),fill="#40546D",width=2)
    total_day=sum((r["day"] for r in rows),Decimal(0))
    total_week=sum((r["week"] for r in rows),Decimal(0))
    for i,row in enumerate(rows,1):
        y=305+(i-1)*72
        if i%2==0:
            d.rounded_rectangle((65,y-10,width-65,y+54),radius=12,fill="#142438")
        d.text((1390,y+22),fa(i),font=font(24,True),fill="#F3F6FB",anchor="mm")
        d.text((1110,y+22),short(row["address"]),font=font(20),fill="#9FB5CE",anchor="rm")
        d.text((760,y+22),amount(row["balance"]),font=font(25,True),fill="#F4F7FC",anchor="rm")
        for key,x in (("day",470),("week",185)):
            val=row[key]
            color="#2BD68B" if val>0 else "#FF6577" if val<0 else "#C6D2E1"
            d.text((x,y+22),amount(val),font=font(23,True),fill=color,anchor="rm")
        d.line((75,y+58,width-75,y+58),fill="#26384E",width=1)
    base=1050
    rtl(d,(width-95,base),"خالص تغییر ۱۰ کیف پول",font(26,True),"#C8D7E8")
    d.text((95,base),f"24h  {amount(total_day)} LAB     |     7d  {amount(total_week)} LAB",font=font(26,True),fill="#2BD68B" if total_day>=0 else "#FF6577",anchor="lm")
    d.text((width-95,1110),"تغییر موجودی به‌تنهایی به معنی خرید یا فروش قطعی نیست",font=font(18),fill="#91A7C0",anchor="ra")
    im.save(OUT,quality=92)


def read_id():
    try:
        return int(STATE_FILE.read_text().strip() or "0")
    except FileNotFoundError:
        return 0


def write_id(value):
    STATE_FILE.parent.mkdir(parents=True,exist_ok=True)
    STATE_FILE.write_text(str(value)+"\\n",encoding="utf-8")


def telegram(method,payload):
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN secret is missing")
    r=requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",data=payload,files={"photo":open(OUT,"rb")} if method=="sendPhoto" else None,timeout=TIMEOUT)
    data=r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {data.get('description')}")
    return data.get("result") or {}


def publish():
    rows=fetch_rows()
    make_image(rows)
    previous=read_id()
    with open(OUT,"rb") as photo:
        response=requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",data={"chat_id":CHAT_ID,"caption":"🐳 دیده‌بان موجودی ۱۰ نهنگ LAB | منبع: LBank"},files={"photo":photo},timeout=TIMEOUT)
    payload=response.json()
    if not payload.get("ok"):
        raise RuntimeError("Telegram sendPhoto failed: "+str(payload.get("description")))
    new_id=payload["result"]["message_id"]
    if previous and previous!=new_id:
        try:
            telegram("deleteMessage",{"chat_id":CHAT_ID,"message_id":previous})
            logging.info("Deleted previous LBank holdings report: %s",previous)
        except Exception as exc:
            logging.warning("Could not delete previous LBank report %s: %s",previous,exc)
    write_id(new_id)
    logging.info("Published LBank holdings image message_id=%s",new_id)


if __name__=="__main__":
    publish()
