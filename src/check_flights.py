import os
import json
import sys
import requests
from datetime import datetime, timezone, timedelta

RAPIDAPI_KEY = os.environ["RAPIDAPI_KEY"]
RAPIDAPI_HOST = "flights-sky.p.rapidapi.com"
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

CACHE_FILE = "price_cache.json"

HEADERS = {
    "x-rapidapi-key": RAPIDAPI_KEY,
    "x-rapidapi-host": RAPIDAPI_HOST,
}

ORIGIN = "SAW"
DESTINATION = "BEG"
DEPART_DATE = "2026-04-30"

TURKEY_TZ = timezone(timedelta(hours=3))


def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
    })
    if not resp.ok:
        print(f"Telegram error: {resp.status_code} {resp.text}")


def search_flights() -> list[dict]:
    """Search one-way flights SAW → BEG using Flights Scraper Sky API."""

    url = f"https://{RAPIDAPI_HOST}/web/flights/search-one-way"
    params = {
        "fromEntityId": ORIGIN,
        "toEntityId": DESTINATION,
        "departDate": DEPART_DATE,
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
        print(f"API returned error: {json.dumps(data, indent=2)[:500]}")
        return []

    context = data.get("data", {}).get("context", {})
    session_id = context.get("sessionId", "")

    itineraries = data.get("data", {}).get("itineraries", [])

    if context.get("status") == "incomplete" and session_id:
        itineraries = poll_incomplete(session_id, itineraries)

    return itineraries


def poll_incomplete(session_id: str, current_itineraries: list[dict], max_retries: int = 3) -> list[dict]:
    """Poll the incomplete endpoint until results are complete."""
    import time

    url = f"https://{RAPIDAPI_HOST}/web/flights/search-incomplete"

    for attempt in range(max_retries):
        time.sleep(2)
        params = {"sessionId": session_id}
        resp = requests.get(url, headers=HEADERS, params=params)
        if not resp.ok:
            print(f"Incomplete poll failed: {resp.status_code}")
            break

        data = resp.json()
        context = data.get("data", {}).get("context", {})
        itineraries = data.get("data", {}).get("itineraries", [])

        if itineraries:
            current_itineraries = itineraries

        if context.get("status") == "complete":
            print(f"Results complete after {attempt + 1} polls")
            break

    return current_itineraries


def filter_evening_flights(itineraries: list[dict]) -> list[dict]:
    """Keep only flights departing Apr 30 after 20:00 or on May 1."""
    filtered = []
    for itin in itineraries:
        legs = itin.get("legs", [])
        if not legs:
            continue

        dep_str = legs[0].get("departure", "")
        if not dep_str:
            continue

        try:
            dep = datetime.fromisoformat(dep_str)
        except ValueError:
            continue

        if (dep.month == 4 and dep.day == 30 and dep.hour >= 20) or \
           (dep.month == 5 and dep.day == 1):
            filtered.append(itin)

    return filtered


def extract_flight_info(itin: dict) -> dict:
    """Extract key fields from an itinerary for comparison and display."""
    leg = itin.get("legs", [{}])[0]
    price_obj = itin.get("price", {})

    carriers = leg.get("carriers", {}).get("marketing", [])
    airline = carriers[0].get("name", "Bilinmiyor") if carriers else "Bilinmiyor"

    return {
        "id": itin.get("id", leg.get("id", "")),
        "price": price_obj.get("raw", 0),
        "price_formatted": price_obj.get("formatted", "N/A"),
        "airline": airline,
        "departure": leg.get("departure", ""),
        "arrival": leg.get("arrival", ""),
        "duration": leg.get("durationInMinutes", 0),
        "stops": leg.get("stopCount", 0),
        "origin": leg.get("origin", {}).get("id", ORIGIN),
        "destination": leg.get("destination", {}).get("id", DESTINATION),
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
    """Format ISO datetime to readable Turkish format."""
    try:
        dt = datetime.fromisoformat(iso_str)
        months = {4: "Nis", 5: "May"}
        return f"{dt.day} {months.get(dt.month, '')} {dt.hour:02d}:{dt.minute:02d}"
    except (ValueError, AttributeError):
        return iso_str


def build_notification(changes: list[str]) -> str:
    now = datetime.now(TURKEY_TZ).strftime("%d/%m %H:%M")
    msg = "<b>✈️ SAW → BEG Uçuş Fiyat Takibi</b>\n\n"
    msg += "\n\n".join(changes)
    msg += f"\n\n🕐 Kontrol: {now}"
    return msg


def main():
    print(f"[{datetime.now(TURKEY_TZ).isoformat()}] Checking flights...")

    try:
        itineraries = search_flights()
    except requests.exceptions.RequestException as e:
        print(f"API request failed: {e}")
        send_telegram(f"⚠️ API hatası: {e}")
        sys.exit(1)

    if not itineraries:
        print("No itineraries returned from API")
        send_telegram("⚠️ API'den uçuş verisi alınamadı. Yanıt boş döndü.")
        sys.exit(0)

    print(f"Found {len(itineraries)} total itineraries")

    evening_flights = filter_evening_flights(itineraries)
    print(f"Filtered to {len(evening_flights)} evening flights (Apr 30 20:00+ / May 1)")

    if not evening_flights:
        all_flights = [extract_flight_info(i) for i in itineraries]
        print("All flight departure times:")
        for f in all_flights:
            print(f"  {f['airline']} - {f['departure']} - {f['price_formatted']}")

    cache = load_cache()
    changes = []
    new_cache = {}

    for itin in evening_flights:
        info = extract_flight_info(itin)
        flight_key = f"{info['airline']}_{info['departure']}_{info['stops']}"
        old = cache.get(flight_key)

        new_cache[flight_key] = {
            "price": info["price"],
            "price_formatted": info["price_formatted"],
            "airline": info["airline"],
            "departure": info["departure"],
            "arrival": info["arrival"],
            "stops": info["stops"],
            "last_checked": datetime.now(TURKEY_TZ).isoformat(),
        }

        dep_display = format_time(info["departure"])
        arr_display = format_time(info["arrival"])
        stop_text = "Direkt" if info["stops"] == 0 else f"{info['stops']} aktarma"

        if old is None:
            changes.append(
                f"🆕 <b>{info['airline']}</b>\n"
                f"   📅 {dep_display} → {arr_display} ({stop_text})\n"
                f"   💰 {info['price_formatted']}"
            )
        elif old["price"] != info["price"]:
            old_price = old["price"]
            new_price = info["price"]
            diff = new_price - old_price

            if diff < 0:
                icon = "📉 DÜŞTÜ"
                diff_text = f"-{abs(diff):.0f}"
            else:
                icon = "📈 ARTTI"
                diff_text = f"+{diff:.0f}"

            changes.append(
                f"{icon}: <b>{info['airline']}</b>\n"
                f"   📅 {dep_display} → {arr_display} ({stop_text})\n"
                f"   💰 {old['price_formatted']} → {info['price_formatted']} ({diff_text} TRY)"
            )

    removed = set(cache.keys()) - set(new_cache.keys())
    for key in removed:
        old = cache[key]
        dep_display = format_time(old["departure"])
        changes.append(
            f"❌ KALDIRILDI: <b>{old['airline']}</b>\n"
            f"   📅 {dep_display} - {old['price_formatted']}"
        )

    if changes:
        msg = build_notification(changes)
        print(f"Sending notification with {len(changes)} changes")
        send_telegram(msg)
    else:
        print("No price changes detected")

    save_cache(new_cache)
    print("Cache updated successfully")


if __name__ == "__main__":
    main()
