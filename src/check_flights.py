"""
SAW -> BEG / TZL direkt uçuş takibi.
Kiwi.com Cheap Flights API (RapidAPI) ile her saat kontrol.
Tüm bulunan direkt uçuşlar Kiwi.com booking linki ile birlikte Telegram'a gönderilir.
"""
import os
import json
import sys
import time
import requests
from datetime import datetime, timezone, timedelta

RAPIDAPI_KEY = os.environ["RAPIDAPI_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

CACHE_FILE = "price_cache.json"
STATE_FILE = "notify_state.json"

KIWI_HOST = "kiwi-com-cheap-flights.p.rapidapi.com"

TURKEY_TZ = timezone(timedelta(hours=3))

ROUTES = [
    {"from": "SAW", "to": "BEG", "label": "SAW → BEG (Belgrad)"},
    {"from": "SAW", "to": "TZL", "label": "SAW → TZL (Tuzla)"},
]

DEPART_DATE_FROM = "2026-04-30"
DEPART_DATE_TO = "2026-05-01"

TELEGRAM_MAX = 3900

INTRO_MESSAGE = """<b>✈️ Uçuş fiyat takibi başladı</b>

Rotalar: SAW → BEG (Belgrad), SAW → TZL (Tuzla)
Hedef: 30 Nisan – 1 Mayıs, sadece <b>direkt</b> uçuşlar.
Kaynak: <b>Kiwi.com</b>

Her saat tam liste + Kiwi.com rezervasyon linki gönderilir."""

HEADERS = {
    "x-rapidapi-key": RAPIDAPI_KEY,
    "x-rapidapi-host": KIWI_HOST,
    "Content-Type": "application/json",
}


def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    })
    if not resp.ok:
        print(f"Telegram error: {resp.status_code} {resp.text}")


def send_telegram_long(text: str):
    parts = []
    buf = ""
    for line in text.split("\n"):
        if len(buf) + len(line) + 1 > TELEGRAM_MAX:
            parts.append(buf)
            buf = line
        else:
            buf = (buf + "\n" + line) if buf else line
    if buf:
        parts.append(buf)
    for i, p in enumerate(parts):
        if len(parts) > 1:
            p = f"<i>({i + 1}/{len(parts)})</i>\n" + p
        send_telegram(p)
        if i < len(parts) - 1:
            time.sleep(1)


def load_notify_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"intro_sent": False}


def save_notify_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def load_cache() -> dict:
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE) as f:
            return json.load(f)
    return {}


def save_cache(cache: dict):
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


def format_time(iso_str: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_str)
        months = {1: "Oca", 2: "Şub", 3: "Mar", 4: "Nis", 5: "May", 6: "Haz",
                  7: "Tem", 8: "Ağu", 9: "Eyl", 10: "Eki", 11: "Kas", 12: "Ara"}
        return f"{dt.day} {months.get(dt.month, '')} {dt.hour:02d}:{dt.minute:02d}"
    except (ValueError, AttributeError):
        return iso_str[:16] if iso_str else "?"


def format_duration(seconds: int) -> str:
    mins = max(1, seconds // 60)
    h, m = divmod(mins, 60)
    return f"{h}s {m}dk" if h else f"{m}dk"


# --- Kiwi API ---

def search_kiwi(route: dict) -> dict:
    url = f"https://{KIWI_HOST}/one-way"
    params = {
        "source": f"Airport:{route['from']}",
        "destination": f"Airport:{route['to']}",
        "currency": "TRY",
        "locale": "tr",
        "adults": "1",
        "children": "0",
        "infants": "0",
        "cabinClass": "ECONOMY",
        "transportTypes": "FLIGHT",
        "limit": "20",
        "fromDate": DEPART_DATE_FROM,
        "toDate": DEPART_DATE_TO,
        "contentProviders": "KIWI",
        "sortBy": "PRICE",
        "sortOrder": "ASCENDING",
    }
    resp = requests.get(url, headers=HEADERS, params=params, timeout=90)

    if resp.status_code == 429:
        raise RuntimeError("API kota limiti aşıldı (429)")
    if resp.status_code == 403:
        raise RuntimeError("API erişim hatası (403) — abonelik kontrol edin")
    resp.raise_for_status()

    data = resp.json()
    if isinstance(data, dict):
        if data.get("error"):
            raise RuntimeError(f"API hatası: {data['error']}")
        if data.get("message") and not data.get("itineraries"):
            raise RuntimeError(f"API: {data['message']}")
    return data


def parse_kiwi_flights(route: dict, data: dict) -> list[dict]:
    """Kiwi itineraries -> direkt uçuş listesi (tüm tarihler dahil)."""
    out = []
    for it in data.get("itineraries") or []:
        sector = it.get("sector") or {}
        segs = sector.get("sectorSegments") or []
        if len(segs) != 1:
            continue
        seg = segs[0].get("segment") or {}
        if seg.get("type") != "FLIGHT":
            continue

        src_info = seg.get("source") or {}
        dst_info = seg.get("destination") or {}
        dep_s = src_info.get("localTime") or ""
        arr_s = dst_info.get("localTime") or ""
        if not dep_s:
            continue

        try:
            dep_dt = datetime.fromisoformat(dep_s)
        except ValueError:
            continue
        if dep_dt.month != 5:
            continue

        carrier = (seg.get("carrier") or {}).get("name") or "?"

        price_amt = it.get("price", {}).get("amount")
        try:
            price_f = float(price_amt)
        except (TypeError, ValueError):
            price_f = 0.0
        price_fmt = f"{price_f:,.0f} TL".replace(",", ".")

        dur_sec = int(sector.get("duration") or seg.get("duration") or 0)

        booking_url = "https://www.kiwi.com/tr/"
        edges = ((it.get("bookingOptions") or {}).get("edges")) or []
        if edges:
            node = edges[0].get("node") or {}
            bu = node.get("bookingUrl") or ""
            if bu.startswith("http"):
                booking_url = bu
            elif bu.startswith("/"):
                booking_url = "https://www.kiwi.com" + bu

        out.append({
            "airline": carrier,
            "departure": dep_s,
            "arrival": arr_s,
            "price": price_f,
            "price_formatted": price_fmt,
            "duration_sec": dur_sec,
            "booking_url": booking_url,
        })
    return out


def price_delta(cache: dict, key: str, price: float) -> str:
    old = cache.get(key)
    if old is None:
        return "🆕"
    old_p = old.get("price")
    if old_p is None:
        return ""
    if abs(old_p - price) < 0.5:
        return ""
    diff = price - old_p
    if diff < 0:
        return f"📉 {diff:,.0f} TL".replace(",", ".")
    return f"📈 +{diff:,.0f} TL".replace(",", ".")


def format_flight_line(idx: int, f: dict, delta: str) -> str:
    dep = format_time(f["departure"])
    arr = format_time(f["arrival"])
    dur = format_duration(f["duration_sec"])
    delta_txt = f" <b>{delta}</b>" if delta else ""
    href = f["booking_url"].replace("&", "&amp;")
    return (
        f"{idx}. <b>{f['airline']}</b>{delta_txt}\n"
        f"   📅 {dep} → {arr} | ⏱ {dur}\n"
        f"   💰 <b>{f['price_formatted']}</b>\n"
        f'   <a href="{href}">Kiwi.com\'da gör</a>'
    )


def process_route(route: dict, cache: dict, new_cache: dict) -> str:
    prefix = f"{route['from']}_{route['to']}"
    header = f"<b>✈️ {route['label']}</b>"

    try:
        raw = search_kiwi(route)
        flights = parse_kiwi_flights(route, raw)
    except Exception as e:
        return f"{header}\n⚠️ {e}"

    flights.sort(key=lambda x: x["price"])

    lines = [header, f"<i>{len(flights)} direkt uçuş bulundu</i>", ""]

    if not flights:
        lines.append("<i>Direkt uçuş bulunamadı.</i>")
    else:
        for i, f in enumerate(flights, 1):
            key = f"{prefix}_{f['airline']}_{f['departure']}"
            delta = price_delta(cache, key, f["price"])
            lines.append(format_flight_line(i, f, delta))

            new_cache[key] = {
                "price": f["price"],
                "price_formatted": f["price_formatted"],
                "airline": f["airline"],
                "departure": f["departure"],
                "last_checked": datetime.now(TURKEY_TZ).isoformat(),
            }

    return "\n".join(lines)


def main():
    now = datetime.now(TURKEY_TZ).strftime("%d/%m/%Y %H:%M")
    print(f"[{now}] Checking flights...")

    notify_state = load_notify_state()
    if not notify_state.get("intro_sent"):
        send_telegram(INTRO_MESSAGE)
        notify_state["intro_sent"] = True
        save_notify_state(notify_state)

    cache = load_cache()
    new_cache: dict = {}
    sections = []

    for route in ROUTES:
        section = process_route(route, cache, new_cache)
        sections.append(section)
        time.sleep(2)

    msg = "<b>Saatlik Uçuş Raporu</b>\n\n"
    msg += f"\n\n{'—' * 15}\n\n".join(sections)
    msg += f"\n\n{'—' * 15}\n🕐 {now}"

    send_telegram_long(msg)
    save_cache(new_cache)
    print("Done.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Fatal: {e}")
        try:
            send_telegram(f"⚠️ Uçuş kontrol hatası:\n{type(e).__name__}: {e}")
        except Exception:
            pass
        sys.exit(1)
