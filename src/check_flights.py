import os
import json
import sys
import time
import requests
from datetime import datetime, timezone, timedelta

RAPIDAPI_KEY = os.environ["RAPIDAPI_KEY"]
RAPIDAPI_HOST = "flights-sky.p.rapidapi.com"
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

CACHE_FILE = "price_cache.json"
STATE_FILE = "notify_state.json"

HEADERS = {
    "x-rapidapi-key": RAPIDAPI_KEY,
    "x-rapidapi-host": RAPIDAPI_HOST,
    "Content-Type": "application/json",
}

ROUTES = [
    {"from": "SAW", "to": "BEG", "label": "SAW → BEG (Belgrad)"},
    {"from": "SAW", "to": "TZL", "label": "SAW → TZL (Tuzla/Saraybosna)"},
]

DEPART_DATES = ["2026-04-30", "2026-05-01"]

TURKEY_TZ = timezone(timedelta(hours=3))

INTRO_MESSAGE = """<b>✈️ Uçuş Fiyat Takibi başladı!</b>

Takip edilen rotalar:
• İstanbul SAW → Belgrad (BEG)
• İstanbul SAW → Tuzla (TZL)

Tarih: 30 Nisan 20:00 sonrası – 1 Mayıs
Sadece <b>direkt</b> uçuşlar izleniyor.

Her kontrolde durum bilgisi gönderilir."""


def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
    })
    if not resp.ok:
        print(f"Telegram error: {resp.status_code} {resp.text}")


def load_notify_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"intro_sent": False}


def save_notify_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def search_flights(origin: str, destination: str, depart_date: str) -> list[dict]:
    url = f"https://{RAPIDAPI_HOST}/web/flights/search-one-way"
    params = {
        "placeIdFrom": origin,
        "placeIdTo": destination,
        "departDate": depart_date,
        "currency": "TRY",
        "market": "TR",
        "locale": "tr-TR",
        "cabinClass": "economy",
        "adults": "1",
    }

    resp = requests.get(url, headers=HEADERS, params=params)
    resp.raise_for_status()
    data = resp.json()

    if not data.get("status"):
        print(f"  API error {origin}->{destination} {depart_date}: {json.dumps(data, indent=2)[:300]}")
        return []

    itineraries_data = data.get("data", {}).get("itineraries", {})
    items = extract_items_from_buckets(itineraries_data)

    context = itineraries_data.get("context", {})
    if context.get("status") == "incomplete":
        session_id = context.get("sessionId", "")
        if session_id:
            items = poll_incomplete(session_id, items)

    return items


def extract_items_from_buckets(itineraries_data: dict) -> list[dict]:
    seen_ids = set()
    items = []
    for bucket in itineraries_data.get("buckets", []):
        for item in bucket.get("items", []):
            item_id = item.get("id", "")
            if item_id and item_id not in seen_ids:
                seen_ids.add(item_id)
                items.append(item)
    return items


def poll_incomplete(session_id: str, current_items: list[dict], max_retries: int = 3) -> list[dict]:
    url = f"https://{RAPIDAPI_HOST}/web/flights/search-incomplete"
    for attempt in range(max_retries):
        time.sleep(3)
        resp = requests.get(url, headers=HEADERS, params={"sessionId": session_id})
        if not resp.ok:
            print(f"  Incomplete poll failed: {resp.status_code}")
            break
        data = resp.json()
        if not data.get("status"):
            break
        itineraries_data = data.get("data", {}).get("itineraries", {})
        new_items = extract_items_from_buckets(itineraries_data)
        if new_items:
            current_items = new_items
        if itineraries_data.get("context", {}).get("status") == "complete":
            print(f"  Complete after {attempt + 1} polls")
            break
    return current_items


def filter_direct_target_flights(items: list[dict]) -> list[dict]:
    """Keep only DIRECT flights departing Apr 30 20:00+ or May 1."""
    filtered = []
    for item in items:
        legs = item.get("legs", [])
        if not legs:
            continue
        leg = legs[0]
        if leg.get("stopCount", 99) != 0:
            continue

        dep_str = leg.get("departure", "")
        if not dep_str:
            continue
        try:
            dep = datetime.fromisoformat(dep_str)
        except ValueError:
            continue

        is_apr30_evening = dep.month == 4 and dep.day == 30 and dep.hour >= 20
        is_may1 = dep.month == 5 and dep.day == 1
        if is_apr30_evening or is_may1:
            filtered.append(item)

    return filtered


def search_route(route: dict) -> list[dict]:
    """Search all dates for a route, return only direct target flights."""
    origin, destination = route["from"], route["to"]
    label = route["label"]
    all_items = []

    for date in DEPART_DATES:
        items = search_flights(origin, destination, date)
        all_items.extend(items)
        print(f"  {label} {date}: {len(items)} flights")
        time.sleep(2)

    direct = filter_direct_target_flights(all_items)
    print(f"  {label} direkt hedef uçuşlar: {len(direct)}")
    return direct


def extract_flight_info(item: dict) -> dict:
    leg = item.get("legs", [{}])[0]
    price_obj = item.get("price", {})
    carriers = leg.get("carriers", {}).get("marketing", [])
    airline_names = [c.get("name", "?") for c in carriers]
    airline = " / ".join(airline_names) if airline_names else "Bilinmiyor"

    return {
        "id": item.get("id", ""),
        "price": price_obj.get("raw", 0),
        "price_formatted": price_obj.get("formatted", "N/A"),
        "airline": airline,
        "departure": leg.get("departure", ""),
        "arrival": leg.get("arrival", ""),
        "duration": leg.get("durationInMinutes", 0),
        "stops": leg.get("stopCount", 0),
    }


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
        months = {4: "Nis", 5: "May"}
        return f"{dt.day} {months.get(dt.month, '')} {dt.hour:02d}:{dt.minute:02d}"
    except (ValueError, AttributeError):
        return iso_str


def format_duration(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return f"{h}s {m}dk" if h else f"{m}dk"


def process_route(route: dict, cache: dict) -> tuple[list[str], dict, int]:
    """Process a single route: compare with cache, return (changes, new_cache_entries, flight_count)."""
    route_key_prefix = f"{route['from']}_{route['to']}"

    try:
        flights = search_route(route)
    except requests.exceptions.RequestException as e:
        print(f"API error for {route['label']}: {e}")
        return [f"⚠️ {route['label']}: API hatası — {e}"], {}, 0

    changes = []
    new_entries = {}

    for item in flights:
        info = extract_flight_info(item)
        flight_key = f"{route_key_prefix}_{info['airline']}_{info['departure']}"
        old = cache.get(flight_key)

        new_entries[flight_key] = {
            "price": info["price"],
            "price_formatted": info["price_formatted"],
            "airline": info["airline"],
            "departure": info["departure"],
            "arrival": info["arrival"],
            "duration": info["duration"],
            "last_checked": datetime.now(TURKEY_TZ).isoformat(),
        }

        dep_display = format_time(info["departure"])
        arr_display = format_time(info["arrival"])
        dur_text = format_duration(info["duration"])

        if old is None:
            changes.append(
                f"  🆕 <b>{info['airline']}</b>\n"
                f"     📅 {dep_display} → {arr_display} | ⏱ {dur_text}\n"
                f"     💰 {info['price_formatted']}"
            )
        elif old["price"] != info["price"]:
            diff = info["price"] - old["price"]
            if diff < 0:
                icon = "📉"
                diff_text = f"-{abs(diff):.0f} TL"
            else:
                icon = "📈"
                diff_text = f"+{diff:.0f} TL"
            changes.append(
                f"  {icon} <b>{info['airline']}</b>\n"
                f"     📅 {dep_display} → {arr_display} | ⏱ {dur_text}\n"
                f"     💰 {old['price_formatted']} → {info['price_formatted']} ({diff_text})"
            )

    old_route_keys = {k for k in cache if k.startswith(route_key_prefix + "_")}
    removed = old_route_keys - set(new_entries.keys())
    for key in removed:
        old = cache[key]
        dep_display = format_time(old["departure"])
        changes.append(
            f"  ❌ <b>{old['airline']}</b> — {dep_display} | {old['price_formatted']} KALDIRILDI"
        )

    return changes, new_entries, len(flights)


def main():
    print(f"[{datetime.now(TURKEY_TZ).isoformat()}] Checking flights...")

    notify_state = load_notify_state()
    if not notify_state.get("intro_sent"):
        send_telegram(INTRO_MESSAGE)
        notify_state["intro_sent"] = True
        save_notify_state(notify_state)
        print("Sent intro message")

    cache = load_cache()
    new_cache = {}
    all_sections = []
    total_flights = 0
    has_changes = False

    for route in ROUTES:
        changes, new_entries, flight_count = process_route(route, cache)
        new_cache.update(new_entries)
        total_flights += flight_count

        section_header = f"<b>{'—' * 20}</b>\n<b>✈️ {route['label']}</b>"

        if changes:
            has_changes = True
            section = section_header + f"\n<i>{flight_count} direkt uçuş</i>\n\n" + "\n\n".join(changes)
        else:
            section = section_header + f"\n✅ Değişiklik yok ({flight_count} direkt uçuş)"

        all_sections.append(section)

    now = datetime.now(TURKEY_TZ).strftime("%d/%m %H:%M")

    msg = "<b>✈️ Uçuş Fiyat Kontrol Raporu</b>\n\n"
    msg += "\n\n".join(all_sections)
    msg += f"\n\n{'—' * 20}\n🕐 Kontrol: {now} | Toplam: {total_flights} direkt uçuş"

    print(f"Sending combined notification (changes: {has_changes})")
    send_telegram(msg)

    save_cache(new_cache)
    print("Cache updated successfully")


if __name__ == "__main__":
    main()
