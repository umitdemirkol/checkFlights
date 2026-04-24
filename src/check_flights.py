"""
SAW -> BEG / TZL direkt uçuş takibi.
Google Flights API (RapidAPI) ile saatlik kontrol.
Her çalışmada tüm direkt uçuşlar fiyat + Google Flights linki ile Telegram'a gönderilir.
"""
import os
import json
import sys
import time
import requests
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

RAPIDAPI_KEY = os.environ["RAPIDAPI_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

CACHE_FILE = "price_cache.json"
STATE_FILE = "notify_state.json"

GF_HOST = "google-flights2.p.rapidapi.com"

TURKEY_TZ = timezone(timedelta(hours=3))

ROUTES = [
    {"from": "SAW", "to": "BEG", "label": "SAW → BEG (Belgrad)"},
    {"from": "SAW", "to": "TZL", "label": "SAW → TZL (Tuzla)"},
]

SEARCH_DATES = ["2026-04-30", "2026-05-01"]

TELEGRAM_MAX = 3900

INTRO_MESSAGE = """<b>✈️ Uçuş fiyat takibi başladı</b>

Rotalar: SAW → BEG (Belgrad), SAW → TZL (Tuzla)
Hedef: 30 Nisan – 1 Mayıs, sadece <b>direkt</b> uçuşlar
Kaynak: <b>Google Flights</b>

Her 2 saatte tam liste gönderilir."""

HEADERS = {
    "x-rapidapi-key": RAPIDAPI_KEY,
    "x-rapidapi-host": GF_HOST,
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
    parts, buf = [], ""
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


def load_state(path: str, default: dict) -> dict:
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def save_state(path: str, data: dict):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def google_flights_url(origin: str, dest: str, date: str) -> str:
    """Google Flights arama linki."""
    return f"https://www.google.com/travel/flights?q=Flights%20from%20{origin}%20to%20{dest}%20on%20{date}%20one%20way"


def format_dep_time(raw: str) -> str:
    """'2026-4-30 15:15' -> '30 Nis 15:15'"""
    months = {1: "Oca", 2: "Şub", 3: "Mar", 4: "Nis", 5: "May", 6: "Haz",
              7: "Tem", 8: "Ağu", 9: "Eyl", 10: "Eki", 11: "Kas", 12: "Ara"}
    try:
        dt = datetime.strptime(raw, "%Y-%m-%d %H:%M")
        return f"{dt.day} {months.get(dt.month, '')} {dt.hour:02d}:{dt.minute:02d}"
    except (ValueError, AttributeError):
        return raw


def format_duration(raw_min: int) -> str:
    h, m = divmod(raw_min, 60)
    return f"{h}s {m}dk" if h else f"{m}dk"


def search_google_flights(origin: str, dest: str, date: str) -> list[dict]:
    """Google Flights API ile tek yön uçuş ara."""
    url = f"https://{GF_HOST}/api/v1/searchFlights"
    params = {
        "departure_id": origin,
        "arrival_id": dest,
        "outbound_date": date,
        "travel_class": "ECONOMY",
        "adults": "1",
        "show_hidden": "1",
        "currency": "TRY",
        "language_code": "tr",
        "country_code": "TR",
        "search_type": "best",
        "type": "1",
    }
    resp = requests.get(url, headers=HEADERS, params=params, timeout=90)

    if resp.status_code == 429:
        raise RuntimeError("Google Flights API kota limiti (429)")
    if resp.status_code == 403:
        raise RuntimeError("API erişim hatası (403)")
    resp.raise_for_status()

    data = resp.json()
    if not data.get("status"):
        msg = data.get("message", "Bilinmeyen hata")
        if isinstance(msg, list):
            msg = "; ".join(str(m) for m in msg)
        raise RuntimeError(f"API: {msg}")

    return data.get("data", {}).get("itineraries", {})


def extract_direct_flights(itineraries: dict, origin: str, dest: str, date: str) -> list[dict]:
    """topFlights + otherFlights'tan sadece direkt (tek segment) uçuşları çıkar."""
    out = []
    seen = set()
    search_url = google_flights_url(origin, dest, date)

    for category in ["topFlights", "otherFlights"]:
        for item in itineraries.get(category, []):
            segments = item.get("flights", [])
            if len(segments) != 1:
                continue

            seg = segments[0]
            dep_time = seg.get("departure_airport", {}).get("time", "")
            arr_time = seg.get("arrival_airport", {}).get("time", "")
            airline = seg.get("airline", "?")
            flight_no = seg.get("flight_number", "")
            duration = item.get("duration", {}).get("raw", 0)
            price = item.get("price")

            dedup_key = f"{airline}_{dep_time}"
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            if price is None:
                continue

            try:
                price_f = float(price)
            except (TypeError, ValueError):
                continue

            price_fmt = f"{price_f:,.0f} TL".replace(",", ".")

            out.append({
                "airline": airline,
                "flight_no": flight_no,
                "departure": dep_time,
                "arrival": arr_time,
                "price": price_f,
                "price_formatted": price_fmt,
                "duration_min": duration,
                "search_url": search_url,
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
    dep = format_dep_time(f["departure"])
    arr = format_dep_time(f["arrival"])
    dur = format_duration(f["duration_min"])
    fno = f" ({f['flight_no']})" if f.get("flight_no") else ""
    delta_txt = f" {delta}" if delta else ""
    href = f["search_url"]
    return (
        f"{idx}. <b>{f['airline']}</b>{fno}{delta_txt}\n"
        f"   📅 {dep} → {arr} | ⏱ {dur}\n"
        f"   💰 <b>{f['price_formatted']}</b>\n"
        f'   <a href="{href}">Google Flights\'ta gör</a>'
    )


def process_route(route: dict, cache: dict, new_cache: dict) -> str:
    prefix = f"{route['from']}_{route['to']}"
    header = f"<b>✈️ {route['label']}</b>"
    all_flights = []
    errors = []

    for date in SEARCH_DATES:
        try:
            itineraries = search_google_flights(route["from"], route["to"], date)
            flights = extract_direct_flights(itineraries, route["from"], route["to"], date)
            all_flights.extend(flights)
            print(f"  {route['label']} {date}: {len(flights)} direkt")
        except Exception as e:
            errors.append(str(e))
            print(f"  {route['label']} {date}: HATA {e}")
        time.sleep(3)

    # Tekrar edenleri kaldır
    seen = set()
    unique = []
    for f in all_flights:
        key = f"{f['airline']}_{f['departure']}"
        if key not in seen:
            seen.add(key)
            unique.append(f)
    unique.sort(key=lambda x: (x["departure"], x["price"]))

    lines = [header]
    if errors:
        lines.append(f"<i>⚠️ {'; '.join(errors)}</i>")
    lines.append(f"<i>{len(unique)} direkt uçuş</i>")
    lines.append("")

    if not unique:
        lines.append("<i>Direkt uçuş bulunamadı.</i>")
    else:
        for i, f in enumerate(unique, 1):
            key = f"{prefix}_{f['airline']}_{f['departure']}"
            delta = price_delta(cache, key, f["price"])
            lines.append(format_flight_line(i, f, delta))

            new_cache[key] = {
                "price": f["price"],
                "price_formatted": f["price_formatted"],
                "airline": f["airline"],
                "flight_no": f["flight_no"],
                "departure": f["departure"],
                "last_checked": datetime.now(TURKEY_TZ).isoformat(),
            }

    return "\n".join(lines)


def main():
    now = datetime.now(TURKEY_TZ).strftime("%d/%m/%Y %H:%M")
    print(f"[{now}] Checking flights...")

    notify_state = load_state(STATE_FILE, {"intro_sent": False})
    if not notify_state.get("intro_sent"):
        send_telegram(INTRO_MESSAGE)
        notify_state["intro_sent"] = True
        save_state(STATE_FILE, notify_state)

    cache = load_state(CACHE_FILE, {})
    new_cache: dict = {}
    sections = []

    for route in ROUTES:
        section = process_route(route, cache, new_cache)
        sections.append(section)
        time.sleep(3)

    msg = "<b>Saatlik Uçuş Raporu</b>\n\n"
    msg += f"\n\n{'—' * 15}\n\n".join(sections)
    msg += f"\n\n{'—' * 15}\n🕐 {now}"

    send_telegram_long(msg)
    save_state(CACHE_FILE, new_cache)
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
