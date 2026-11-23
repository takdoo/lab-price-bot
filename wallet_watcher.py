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
from eth_hash.auto import keccak
from PIL import Image, ImageDraw, ImageFont

CONTRACT = "0x7ec43cf65f1663f820427c62a5780b8f2e25593a"
PLATFORM = os.getenv("CMC_PLATFORM", "bsc")
CMC_API_KEY = os.getenv("CMC_PRO_API_KEY", "").strip()
CMC_URL = (
    "https://pro-api.coinmarketcap.com/v1/dex/holders/list"
    if CMC_API_KEY
    else "https://pro-api.coinmarketcap.com/public-api/v1/dex/holders/list"
)
STATE_FILE = Path("top_holders.json")
OUTPUT_FILE = Path("lab_whale_watch.jpg")
STATUS_OUTPUT_FILE = Path("lab_whale_status.jpg")
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "@lab_chats").strip()
ALERT_THRESHOLD = Decimal(os.getenv("WHALE_ALERT_LAB", "1000000"))
TOP_HOLDERS_COUNT = 10
LAST_CHAIN_META = {}
TIMEOUT = (10, 30)
BSC_RPC_URLS = [u.strip() for u in os.getenv("BSC_RPC_URLS", "https://bsc-dataseed.binance.org,https://bsc-dataseed1.defibit.io,https://bsc-dataseed1.ninicoin.io").split(",") if u.strip()]

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


def _rpc_call(url, method, params):
    response = requests.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(str(payload["error"]))
    if "result" not in payload:
        raise RuntimeError("BSC RPC response has no result")
    return payload["result"]


def fetch_onchain_balances(addresses):
    """Read raw balances and EIP-8056 UI multiplier from one BSC block."""
    errors = []
    for rpc_url in BSC_RPC_URLS:
        try:
            block_hex = _rpc_call(rpc_url, "eth_blockNumber", [])
            block_number = int(block_hex, 16)
            block_tag = hex(block_number)
            multiplier_selector = "0x" + keccak(b"uiMultiplier()")[:4].hex()
            decimals_selector = "0x313ce567"
            multiplier_raw = int(_rpc_call(rpc_url, "eth_call", [{"to": CONTRACT, "data": multiplier_selector}, block_tag]), 16)
            decimals = int(_rpc_call(rpc_url, "eth_call", [{"to": CONTRACT, "data": decimals_selector}, block_tag]), 16)
            if multiplier_raw <= 0 or decimals < 0 or decimals > 36:
                raise RuntimeError(f"Invalid on-chain multiplier/decimals: {multiplier_raw}/{decimals}")
            multiplier = Decimal(multiplier_raw) / Decimal(10**18)
            result = {}
            for address in addresses:
                selector = "0x70a08231" + address.lower().replace("0x", "").rjust(64, "0")
                raw = int(_rpc_call(rpc_url, "eth_call", [{"to": CONTRACT, "data": selector}, block_tag]), 16)
                raw_amount = Decimal(raw) / Decimal(10**decimals)
                ui_amount = raw_amount * multiplier
                result[address.lower()] = str(ui_amount)
            logging.info("BSC direct balances verified at block %s; UI multiplier=%s; decimals=%s; RPC=%s", block_number, multiplier, decimals, rpc_url)
            return result, {"source": "BSC RPC balanceOf × uiMultiplier", "block_number": block_number, "ui_multiplier": str(multiplier), "decimals": decimals}
        except Exception as exc:
            errors.append(f"{rpc_url}: {exc}")
            logging.warning("BSC RPC failed: %s", exc)
    raise RuntimeError("Could not verify LAB balances directly on BSC; refusing stale CMC balances. " + " | ".join(errors))


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
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if CMC_API_KEY:
            headers["X-CMC_PRO_API_KEY"] = CMC_API_KEY
        response = requests.post(
            CMC_URL,
            json={"tokenAddress": CONTRACT, "platform": platform, "tag": "tag_all"},
            headers=headers,
            timeout=TIMEOUT,
        )
        if not response.ok:
            detail = response.text[:1200].replace("\\n", " ")
            if response.status_code == 429 and not CMC_API_KEY:
                raise RuntimeError(
                    "CoinMarketCap anonymous-access limit reached (HTTP 429). "
                    "Add a free CMC API key as the GitHub Actions secret CMC_PRO_API_KEY."
                )
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
    if len(holders) < TOP_HOLDERS_COUNT:
        raise ValueError(
            f"فقط {len(holders)} کیف پول معتبر دریافت شد؛ "
            f"برای گزارش {TOP_HOLDERS_COUNT} هولدر کافی نیست"
        )
    # CMC supplies candidate addresses only. Never trust its cached balances:
    # replace every balance with a same-block BSC contract read and UI multiplier.
    onchain, chain_meta = fetch_onchain_balances([item["address"] for item in holders])
    for item in holders:
        item["balance"] = onchain[item["address"]]
    holders.sort(key=lambda item: Decimal(item["balance"]), reverse=True)
    global LAST_CHAIN_META
    LAST_CHAIN_META = chain_meta
    logging.info("Using live BSC balances for %s CMC candidate addresses", len(holders))
    return holders[:TOP_HOLDERS_COUNT]


def _decimal_or_none(value):
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def fetch_futures_data():
    """Fetch LABUSDT futures metrics from free public exchange endpoints."""
    pairs = []
    errors = []
    base = "https://fapi.binance.com"
    try:
        r = requests.get(base + "/fapi/v1/ticker/24hr", params={"symbol": "LABUSDT"}, timeout=TIMEOUT)
        r.raise_for_status()
        ticker = r.json()
        r = requests.get(base + "/fapi/v1/openInterest", params={"symbol": "LABUSDT"}, timeout=TIMEOUT)
        r.raise_for_status()
        oi = r.json()
        r = requests.get(base + "/fapi/v1/premiumIndex", params={"symbol": "LABUSDT"}, timeout=TIMEOUT)
        r.raise_for_status()
        funding = r.json()
        oi_change = None
        try:
            r = requests.get(base + "/futures/data/openInterestHist", params={"symbol": "LABUSDT", "period": "1h", "limit": 25}, timeout=TIMEOUT)
            r.raise_for_status()
            hist = r.json()
            if isinstance(hist, list) and len(hist) >= 2:
                first = _decimal_or_none(hist[0].get("sumOpenInterestValue"))
                last = _decimal_or_none(hist[-1].get("sumOpenInterestValue"))
                if first and first != 0 and last is not None:
                    oi_change = (last - first) / first * Decimal(100)
        except (requests.RequestException, ValueError, TypeError, AttributeError):
            logging.warning("Binance OI history unavailable")
        oi_amount = _decimal_or_none(oi.get("openInterest"))
        mark = _decimal_or_none(funding.get("markPrice"))
        pairs.append({"exchange": "Binance", "pair": "LABUSDT", "volume_usd": _decimal_or_none(ticker.get("quoteVolume")) or Decimal(0), "open_interest_usd": (oi_amount * mark if oi_amount is not None and mark is not None else Decimal(0)), "oi_change_24h": oi_change, "funding_rate": _decimal_or_none(funding.get("lastFundingRate")), "long_liquidation_usd": None, "short_liquidation_usd": None})
    except (requests.RequestException, ValueError, TypeError, AttributeError) as exc:
        errors.append("Binance: " + str(exc))
        logging.warning("Binance futures data unavailable: %s", exc)
    base = "https://api.bybit.com"
    try:
        r = requests.get(base + "/v5/market/tickers", params={"category": "linear", "symbol": "LABUSDT"}, timeout=TIMEOUT)
        r.raise_for_status()
        payload = r.json()
        if str(payload.get("retCode")) != "0":
            raise RuntimeError(str(payload.get("retMsg", "Bybit ticker error")))
        rows = (payload.get("result") or {}).get("list") or []
        if rows:
            row = rows[0]
            oi_change = None
            try:
                r = requests.get(base + "/v5/market/open-interest", params={"category": "linear", "symbol": "LABUSDT", "intervalTime": "1h", "limit": 25}, timeout=TIMEOUT)
                r.raise_for_status()
                hist = (r.json().get("result") or {}).get("list") or []
                if len(hist) >= 2:
                    first = _decimal_or_none(hist[-1].get("openInterest"))
                    last = _decimal_or_none(hist[0].get("openInterest"))
                    if first and first != 0 and last is not None:
                        oi_change = (last - first) / first * Decimal(100)
            except (requests.RequestException, ValueError, TypeError, AttributeError):
                logging.warning("Bybit OI history unavailable")
            pairs.append({"exchange": "Bybit", "pair": "LABUSDT", "volume_usd": _decimal_or_none(row.get("turnover24h")) or Decimal(0), "open_interest_usd": _decimal_or_none(row.get("openInterestValue")) or Decimal(0), "oi_change_24h": oi_change, "funding_rate": _decimal_or_none(row.get("fundingRate")), "long_liquidation_usd": None, "short_liquidation_usd": None})
    except (requests.RequestException, ValueError, TypeError, AttributeError, RuntimeError) as exc:
        errors.append("Bybit: " + str(exc))
        logging.warning("Bybit futures data unavailable: %s", exc)
    if not pairs:
        logging.warning("No public LAB futures data: %s", "; ".join(errors))
        return None
    return pairs

def format_usd(value):
    amount = Decimal(str(value))
    if abs(amount) >= Decimal("1000000"):
        return f"${amount / Decimal('1000000'):,.2f}M"
    if abs(amount) >= Decimal("1000"):
        return f"${amount / Decimal('1000'):,.1f}K"
    return f"${amount:,.0f}"


def futures_caption(pairs):
    if pairs is None:
        return "📊 فیوچرز LAB: دریافت داده از API عمومی صرافی‌ها فعلاً ناموفق بود."
    if not pairs:
        return "📊 فیوچرز LAB: قرارداد پشتیبانی‌شده‌ای برنگشت."
    total_volume = sum((item["volume_usd"] for item in pairs), Decimal(0))
    total_oi = sum((item["open_interest_usd"] for item in pairs), Decimal(0))
    known_oi = [item["oi_change_24h"] for item in pairs if item["oi_change_24h"] is not None]
    weighted_funding = [(item["funding_rate"], item["open_interest_usd"]) for item in pairs if item["funding_rate"] is not None]
    oi_text = f"{sum(known_oi) / len(known_oi):+.2f}% میانگین صرافی‌ها" if known_oi else "نامشخص"
    funding_text = "نامشخص"
    if weighted_funding:
        weight = sum((oi for _, oi in weighted_funding), Decimal(0))
        if weight:
            rate = sum((rate * oi for rate, oi in weighted_funding), Decimal(0)) / weight
            funding_text = f"{rate * Decimal(100):+.4f}% میانگین وزنی OI"
    return (f"📊 فیوچرز LAB ({len(pairs)} بازار عمومی): حجم ۲۴ساعته {format_usd(total_volume)} | "
            f"OI {format_usd(total_oi)} | تغییر OI: {oi_text} | فاندینگ: {funding_text} | "
            "لیکوییدیشن: از API عمومی این نسخه دریافت نمی‌شود.")

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
    # DejaVu Sans has reliable Persian + Latin coverage on GitHub Ubuntu runners.
    # Prefer it over Noto Naskh here to avoid missing-glyph tofu in the generated report.
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/vazirmatn/Vazirmatn-Bold.ttf" if bold else "/usr/share/fonts/truetype/vazirmatn/Vazirmatn-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf" if bold else "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size=size, layout_engine=ImageFont.Layout.RAQM)
            except (AttributeError, ValueError):
                return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def rtl_text(draw, xy, text, font, fill, anchor="ra"):
    # Use Pillow's HarfBuzz/FriBidi path when available.
    try:
        draw.text(xy, text, font=font, fill=fill, anchor=anchor, direction="rtl", language="fa")
        return
    except (TypeError, ValueError):
        pass

    # Pillow wheels without libraqm cannot shape Arabic. Reshape and reorder
    # explicitly instead of drawing isolated Arabic glyphs.
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        visual_text = get_display(arabic_reshaper.reshape(text))
        draw.text(xy, visual_text, font=font, fill=fill, anchor=anchor)
    except ImportError as exc:
        raise RuntimeError(
            "Persian text rendering needs libraqm or arabic-reshaper/python-bidi"
        ) from exc


def create_graphic(changes):
    width, height = 1200, 3000
    image = Image.new("RGB", (width, height), "#0B1220")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((28, 28, width - 28, height - 28), radius=38, fill="#111C2E", outline="#263B56", width=3)
    draw.rounded_rectangle((55, 55, width - 55, 250), radius=30, fill="#172840")
    rtl_text(draw, (width - 90, 105), "دیده‌بان نهنگ‌های LAB", get_font(58, True), "#F3F7FF")
    rtl_text(draw, (width - 90, 180), "گزارش تغییر موجودی ۲۰ کیف پول برتر", get_font(30), "#A9BED8")
    date_text, time_text = iran_time()
    draw.text((90, 215), f"{fa_digits(date_text)}  |  {fa_digits(time_text)}", font=get_font(23), fill="#90A9C5", anchor="ls")

    if not changes:
        rtl_text(draw, (width - 90, 380), "تغییر تازه‌ای ثبت نشده است", get_font(40, True), "#DCE8F7")
    else:
        y = 285
        magnitudes = [abs(item["delta"]) for item in changes if item["delta"] is not None and item["delta"] != 0]
        max_magnitude = max(magnitudes, default=Decimal("1"))
        for item in changes[:TOP_HOLDERS_COUNT]:
            positive = item["delta"] is not None and item["delta"] > 0
            negative = item["delta"] is not None and item["delta"] < 0
            accent = "#29E69A" if positive else "#FF6378" if negative else "#F4C66A"
            panel = "#142C31" if positive else "#321F2B" if negative else "#302B20"
            draw.rounded_rectangle((65, y, width - 65, y + 108), radius=22, fill=panel, outline=accent, width=2)
            rank = item["new_rank"] or item["old_rank"] or 0
            title = f"کیف پول رتبه {fa_digits(rank)}"
            if item["entered"]:
                state = "ورود به ۲۰ کیف پول برتر"
            elif item["left"]:
                state = "خروج از ۲۰ کیف پول برتر"
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
    draw.text((90, height - 112), "منبع داده: BNB Chain RPC", font=get_font(18), fill="#7189A5", anchor="ls")
    draw.text((90, height - 58), "BSC  •  LAB", font=get_font(23, True), fill="#29E69A", anchor="ls")
    rtl_text(draw, (width - 90, height - 58), "گروه تلگرامی @lab_chats", get_font(23), "#DCE8F7")
    image.save(OUTPUT_FILE, "JPEG", quality=94, optimize=True)
    return OUTPUT_FILE


def send_to_telegram(image_path, changes, futures_pairs=None):
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN تنظیم نشده است")
    date_text, time_text = iran_time()
    caption = (
        "<b>🐳 دیده‌بان نهنگ‌های LAB</b>\n"
        f"📅 {date_text} | 🕒 {time_text} (ایران)\n"
        f"🔎 تعداد تغییرات شناسایی‌شده: <b>{len(changes)}</b>\n"
        f"⛓ بلاک BSC: <b>{LAST_CHAIN_META.get('block_number', 'نامشخص')}</b> | ضریب نمایش: <b>{LAST_CHAIN_META.get('ui_multiplier', 'نامشخص')}</b>\n"
        "ℹ️ تغییر موجودی الزاماً به معنی خرید یا فروش نیست.\n"
        + futures_caption(futures_pairs) + "\n"
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
    message_id = result["result"]["message_id"]
    logging.info("گزارش تغییر کیف پول‌ها در گروه ارسال شد")
    return message_id


def delete_telegram_message(message_id):
    """Delete only a previously tracked bot message."""
    if not message_id:
        return True
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage"
    try:
        response = requests.post(
            url,
            data={"chat_id": CHAT_ID, "message_id": message_id},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        result = response.json()
        if result.get("ok"):
            logging.info("پیام قبلی دیده‌بان حذف شد: %s", message_id)
            return True
        description = str(result.get("description") or "")
        if "message to delete not found" in description.lower():
            return True
        logging.warning("پیام قبلی دیده‌بان حذف نشد: %s", description)
    except (requests.RequestException, ValueError) as exc:
        logging.warning("حذف پیام قبلی دیده‌بان ناموفق بود: %s", exc)
    return False


def build_report_rows(previous, holders):
    """Return each current top-ten wallet and its balance delta from the prior snapshot."""
    old_ranks = {address: rank for rank, address in enumerate(previous, start=1)}
    rows = []
    for rank, item in enumerate(holders, start=1):
        address = item["address"]
        balance = Decimal(str(item["balance"]))
        old_value = previous.get(address)
        old_balance = Decimal(str(old_value)) if old_value is not None else None
        rows.append({"rank": rank, "old_rank": old_ranks.get(address), "address": address,
                     "balance": balance, "delta": balance - old_balance if old_balance is not None else None})
    return rows


def create_status_graphic(holders, previous=None, first_run=False):
    previous = previous or {}
    rows = build_report_rows(previous, holders)
    width, height = 1200, 1720
    image = Image.new("RGB", (width, height), "#07111F")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((24, 24, width - 24, height - 24), radius=42, fill="#101D30", outline="#29415F", width=3)
    draw.rounded_rectangle((55, 55, width - 55, 245), radius=32, fill="#172B45")
    rtl_text(draw, (width - 90, 115), "دیده‌بان نهنگ‌های LAB", get_font(58, True), "#F5F8FF")
    rtl_text(draw, (width - 90, 190), "موجودی ۱۰ کیف پول بزرگ و مقایسه ۵ دقیقه‌ای", get_font(29), "#AFC4DE")
    date_text, time_text = iran_time()
    draw.text((90, 205), f"{fa_digits(date_text)}  |  {fa_digits(time_text)}", font=get_font(23), fill="#AFC4DE", anchor="ls")
    changed = sum(1 for row in rows if row["delta"] is not None and row["delta"] != 0)
    headline = "ثبت موجودی اولیه؛ مقایسه از نوبت بعد" if first_run else (f"{fa_digits(changed)} کیف پول دارای تغییر موجودی" if changed else "در ۵ دقیقه اخیر تغییری در موجودی ثبت نشد")
    rtl_text(draw, (width - 90, 295), headline, get_font(31, True), "#F4C66A" if changed or first_run else "#29E69A")
    y = 345
    for row in rows:
        delta = row["delta"]
        if delta is None:
            accent, panel, delta_text = "#F4C66A", "#302A20", "در snapshot قبلی جزو ۱۰ تای اول نبود"
        elif delta > 0:
            accent, panel, delta_text = "#29E69A", "#17352F", "+" + fmt_amount(delta) + " LAB"
        elif delta < 0:
            accent, panel, delta_text = "#FF6378", "#321F2B", "−" + fmt_amount(abs(delta)) + " LAB"
        else:
            accent, panel, delta_text = "#AFC4DE", "#19283A", "بدون تغییر"
        draw.rounded_rectangle((65, y, width - 65, y + 112), radius=22, fill=panel, outline=accent, width=2)
        rtl_text(draw, (width - 95, y + 35), f"رتبه {fa_digits(row['rank'])}  |  {fmt_amount(row['balance'])} LAB", get_font(27, True), "#F4F7FC")
        rtl_text(draw, (width - 95, y + 80), delta_text, get_font(24, True), accent)
        short = row["address"][:8] + "…" + row["address"][-6:]
        draw.text((95, y + 43), short, font=get_font(20), fill="#9EB3CC", anchor="ls")
        if row["old_rank"] is not None and row["old_rank"] != row["rank"]:
            draw.text((95, y + 83), f"رتبه قبلی: {fa_digits(row['old_rank'])}", font=get_font(18), fill="#F4C66A", anchor="ls")
        y += 121
    draw.line((75, 1585, width - 75, 1585), fill="#2A3D55", width=2)
    rtl_text(draw, (width - 90, 1630), "تغییر موجودی الزاماً به معنی خرید یا فروش نیست", get_font(24), "#A9BED8")
    draw.text((90, 1630), "منبع: BNB Chain RPC | LAB • UI-adjusted", font=get_font(18), fill="#7189A5", anchor="ls")
    image.save(STATUS_OUTPUT_FILE, "JPEG", quality=95, optimize=True)
    return STATUS_OUTPUT_FILE


def send_status(holders, changes, previous, first_run=False, futures_pairs=None):
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN تنظیم نشده است")
    date_text, time_text = iran_time()
    balance_changes = sum(1 for item in changes if item["delta"] is not None and item["delta"] != 0)
    rank_changes = sum(1 for item in changes if item["old_rank"] != item["new_rank"])
    status_line = "موجودی اولیه ثبت شد؛ مقایسه از اجرای بعدی آغاز می‌شود." if first_run else f"{fa_digits(balance_changes)} تغییر موجودی و {fa_digits(rank_changes)} تغییر رتبه"
    caption = (
        "<b>🐳 دیده‌بان نهنگ‌های LAB | ۱۰ کیف پول برتر</b>\n"
        f"📅 {date_text} | 🕒 {time_text} (ایران)\n"
        f"🔎 {status_line}\n"
        "📌 موجودی هر آدرس با snapshot قبلی (۵ دقیقه قبل) مقایسه شده است.\n"
        "ℹ️ تغییر موجودی به‌تنهایی اثبات خرید یا فروش نیست.\n"
        + futures_caption(futures_pairs) + "\n"
        "👥 @lab_chats"
    )
    image_path = create_status_graphic(holders, previous=previous, first_run=first_run)
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    with open(image_path, "rb") as photo:
        response = requests.post(url, data={"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"}, files={"photo": photo}, timeout=TIMEOUT)
    response.raise_for_status()
    result = response.json()
    if not result.get("ok"):
        raise RuntimeError(f"Telegram status sendPhoto failed: {result.get('description')}")
    message_id = result["result"]["message_id"]
    logging.info("گزارش ۱۰ کیف پول LAB ارسال شد؛ message_id=%s", message_id)
    return message_id

def save_state(holders, status_message_id, pending_delete_message_ids=None):
    payload = {
        "holders": {item["address"]: item["balance"] for item in holders},
        "status_message_id": status_message_id,
        "pending_delete_message_ids": list(pending_delete_message_ids or []),
        "updated_at": datetime.now(ZoneInfo("UTC")).isoformat(),
        "chain_meta": LAST_CHAIN_META,
    }
    STATE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    current = fetch_holders()
    try:
        futures_pairs = fetch_futures_data()
    except Exception:
        logging.exception("CoinGlass futures data could not be fetched; continuing wallet watch")
        futures_pairs = None
    state = load_state()
    previous = state.get("holders", {})
    if not isinstance(previous, dict):
        previous = {}
    # Migrate the old 20-wallet snapshot without false exits for ranks 11-20.
    previous = dict(list(previous.items())[:TOP_HOLDERS_COUNT])
    first_run = not previous
    changes = compare(previous, current) if previous else []
    new_status_id = send_status(current, changes, previous, first_run=first_run, futures_pairs=futures_pairs)

    # Carry forward old alert IDs and retry any message whose deletion failed.
    pending = state.get("pending_delete_message_ids", [])
    if not isinstance(pending, list):
        pending = []
    old_alerts = state.get("alert_message_ids", [])
    if not isinstance(old_alerts, list):
        old_alerts = [state.get("alert_message_id")]
    pending.extend(old_alerts)
    old_status_id = state.get("status_message_id")
    if old_status_id:
        pending.append(old_status_id)

    remaining, seen = [], set()
    for message_id in pending:
        if not message_id or message_id == new_status_id or message_id in seen:
            continue
        seen.add(message_id)
        if not delete_telegram_message(message_id):
            remaining.append(message_id)
    save_state(current, new_status_id, remaining)
    logging.info(
        "گزارش ۱۰ کیف پول تکمیل شد؛ تغییر موجودی=%s، تغییر رتبه=%s، پیام‌های در انتظار حذف=%s",
        sum(1 for item in changes if item["delta"] is not None and item["delta"] != 0),
        sum(1 for item in changes if item["old_rank"] != item["new_rank"]),
        len(remaining),
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logging.exception("دیده‌بان کیف پول‌ها اجرا نشد")
        sys.exit(1)
