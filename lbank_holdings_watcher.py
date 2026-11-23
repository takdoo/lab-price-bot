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

URL = "https://www.lbank.com/price/lab/funds-holdings"  # Official LBank holdings source
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "@labcoinss").strip()
STATE_FILE = Path(os.getenv("LBANK_STATE_FILE", ".lbank-state/message_id.txt"))
OUT = Path("lab_lbank_holdings.jpg")
TIMEOUT = (15, 45)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def dec(value):
    token = str(value or "").replace(",", "").replace("−", "-").strip().upper()
    suffix = token[-1] if token and token[-1] in "KMB" else ""
    number_text = token[:-1] if suffix else token
    number = Decimal(number_text)
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
    address_pattern = re.compile(r"0x[a-f0-9]{4}[.]{3}[a-f0-9]{4,}", re.I)
    matches = list(address_pattern.finditer(section))
    number_pattern = re.compile(r"[-+]?[0-9][0-9,.]*[KMB]?", re.I)
    for index, address_match in enumerate(matches):
        chunk_end = matches[index + 1].start() if index + 1 < len(matches) else len(section)
        chunk = section[address_match.end():chunk_end]
        values = number_pattern.findall(chunk)
        if len(values) < 4:
            continue
        try:
            rows.append({
                "address": address_match.group(0),
                "balance": dec(values[0]),
                "day": dec(values[2]),
                "week": dec(values[3]),
            })
        except (ValueError, InvalidOperation):
            continue
    if rows:
        return rows[:10]
    raise RuntimeError(f"LBank rows could not be parsed: matches={len(matches)}")


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


def font(size, bold=False, latin=False):
    weight = "Bold" if bold else "Regular"
    if latin:
        paths = [f"/usr/share/fonts/truetype/dejavu/DejaVuSans-{weight}.ttf", f"/usr/share/fonts/truetype/liberation2/LiberationSans-{weight}.ttf"]
    else:
        paths = [
            f"/usr/share/fonts/truetype/vazirmatn/Vazirmatn-{weight}.ttf",
            f"/usr/share/fonts/truetype/noto/NotoSansArabic-{weight}.ttf",
            f"/usr/share/fonts/truetype/noto/NotoNaskhArabic-{weight}.ttf",
            f"/usr/share/fonts/truetype/dejavu/DejaVuSans-{weight}.ttf",
        ]
    for p in paths:
        if Path(p).exists():
            try:
                candidate = ImageFont.truetype(p, size, layout_engine=ImageFont.Layout.RAQM)
                probe = "کیف پول موجودی تغییر منبع داده"
                if latin or all(candidate.getmask(ch).getbbox() is not None for ch in probe if ch.strip()):
                    return candidate
            except Exception:
                continue
    raise RuntimeError("No installed font with complete Persian glyph coverage")


def rtl(draw, xy, text, fnt, fill, anchor="ra"):
    draw.text(xy, str(text), font=fnt, fill=fill, anchor=anchor, direction="rtl", language="fa")


def make_image(rows):
    # Instagram Story canvas: 1080 x 1920, designed for phone screens.
    width, height = 1080, 1920
    im = Image.new("RGB", (width, height), "#07111F")
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((22, 22, width-22, height-22), radius=42, fill="#0D1B2D", outline="#294968", width=3)
    d.rounded_rectangle((48, 48, width-48, 292), radius=34, fill="#142B45", outline="#315779", width=2)
    d.ellipse((830, 72, 1010, 252), fill="#1B3C5C")
    d.text((920, 142), "LAB", font=font(76, True, latin=True), fill="#F6C65B", anchor="mm")
    rtl(d, (785, 142), "دیده‌بان نهنگ‌ها", font(48, True), "#FFFFFF")
    rtl(d, (960, 218), "۱۰ کیف پول بزرگ شبکه BNB", font(27), "#B8CBE0")
    now = datetime.now(ZoneInfo("Asia/Tehran"))
    jalali = jdatetime.datetime.fromgregorian(datetime=now)
    stamp = fa(jalali.strftime("%Y/%m/%d")) + "  |  " + fa(now.strftime("%H:%M"))
    d.text((92, 220), stamp, font=font(24, True, latin=True), fill="#C5D5E7", anchor="lm")

    total_day = sum((r["day"] for r in rows), Decimal(0))
    total_week = sum((r["week"] for r in rows), Decimal(0))
    d.rounded_rectangle((52, 322, 528, 510), radius=28, fill="#13283E", outline="#2B4B68", width=2)
    d.rounded_rectangle((552, 322, 1028, 510), radius=28, fill="#13283E", outline="#2B4B68", width=2)
    rtl(d, (490, 365), "خالص تغییر ۲۴ ساعته", font(27, True), "#D5E3F1")
    rtl(d, (990, 365), "خالص تغییر ۷ روزه", font(27, True), "#D5E3F1")
    color_day = "#36D995" if total_day >= 0 else "#FF687A"
    color_week = "#36D995" if total_week >= 0 else "#FF687A"
    d.text((290, 445), amount(total_day), font=font(51, True, latin=True), fill=color_day, anchor="mm")
    d.text((790, 445), amount(total_week), font=font(51, True, latin=True), fill=color_week, anchor="mm")
    d.text((490, 482), "LAB", font=font(19, True, latin=True), fill="#9FB6CD", anchor="ra")
    d.text((990, 482), "LAB", font=font(19, True, latin=True), fill="#9FB6CD", anchor="ra")

    rtl(d, (1000, 555), "رتبه‌بندی ۱۰ دارنده بزرگ", font(31, True), "#F1F6FC")
    d.line((72, 580, 1008, 580), fill="#36516C", width=2)
    card_top, card_h, gap = 600, 104, 7
    for i, row in enumerate(rows, 1):
        y = card_top + (i-1)*(card_h+gap)
        d.rounded_rectangle((54, y, 1026, y+card_h), radius=22,
                            fill="#14283D" if i % 2 else "#102237", outline="#29435D", width=2)
        d.rounded_rectangle((76, y+17, 142, y+80), radius=18, fill="#254766")
        d.text((109, y+49), fa(i), font=font(29, True), fill="#F6C65B", anchor="mm")
        d.text((170, y+33), short(row["address"]), font=font(19, False, latin=True), fill="#AFC4DA", anchor="lm")
        rtl(d, (990, y+36), "موجودی", font(19, True), "#AFC4DA")
        d.text((990, y+76), amount(row["balance"]), font=font(35, True, latin=True), fill="#FFFFFF", anchor="rm")
        day_color = "#35D58D" if row["day"] > 0 else "#FF6577" if row["day"] < 0 else "#C5D2E0"
        week_color = "#35D58D" if row["week"] > 0 else "#FF6577" if row["week"] < 0 else "#C5D2E0"
        d.text((170, y+83), "24h", font=font(17, True, latin=True), fill="#91A9C2", anchor="lm")
        d.text((222, y+83), amount(row["day"]), font=font(22, True, latin=True), fill=day_color, anchor="lm")
        d.text((405, y+83), "7d", font=font(17, True, latin=True), fill="#91A9C2", anchor="lm")
        d.text((452, y+83), amount(row["week"]), font=font(22, True, latin=True), fill=week_color, anchor="lm")

    footer_y = card_top + 10*(card_h+gap) + 4
    d.rounded_rectangle((54, footer_y, 1026, footer_y+112), radius=24, fill="#172E46", outline="#345675", width=2)
    rtl(d, (990, footer_y+38), "منبع داده: LBank Token Holdings", font(23, True), "#E4EDF7")
    rtl(d, (990, footer_y+78), "به‌روزرسانی خودکار هر ۵ دقیقه", font(21), "#AFC4DA")
    rtl(d, (540, 1840), "تغییر موجودی لزوماً به معنی خرید یا فروش قطعی نیست", font(20), "#829AB4", anchor="mm")
    im.save(OUT, quality=94, optimize=True)


def read_id():
    try:
        raw = STATE_FILE.read_text().strip().replace("\\n", "").strip()
        return int(raw or "0")
    except FileNotFoundError:
        return 0


def write_id(value):
    STATE_FILE.parent.mkdir(parents=True,exist_ok=True)
    STATE_FILE.write_text(str(value) + "\n", encoding="utf-8")


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
