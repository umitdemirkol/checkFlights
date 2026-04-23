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

INTRO_MESSAGE = """<b>✈️ SAW → BEG fiyat takibi başladı</b>

Bu grupta İstanbul Sabiha Gökçen (SAW) → Belgrad (BEG) gidiş uçuşları izleniyor.
Hedef: 30 Nisan 20:00 sonrası ve 1 Mayıs kalkışları.

Her kontrolde fiyat değişirse ayrıntılı mesaj; değişmezse kısa “değişiklik yok” özeti gönderilir."""

HEADERS = {
    "x-rapidapi-key": RAPIDAPI_KEY,
    "x-rapidapi-host": RAPIDAPI_HOST,
    "Content-Type": "application/json",
}

ORIGIN = "SAW"
DESTINATION = "BEG"
DEPART_DATES = ["2026-04-30", "2026-05-01"]

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


def load_notify_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"intro_sent": False}


def save_notify_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def search_flights_for_date(depart_date: str) -> list[dict]:
    """Search one-way flights SAW -> BEG for a specific date."""
    url = f"https://{RAPIDAPI_HOST}/web/flights/search-one-way"
    params = {
        "placeIdFrom": ORIGIN,
        "placeIdTo": DESTINATION,
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
        print(f"API error for {depart_date}: {json.dumps(data, indent=2)[:500]}")
        return []

    itineraries_data = data.get("data", {}).get("itineraries", {})
    items = extract_items_from_buckets(itineraries_data)

    context = itineraries_data.get("context", {})
    if context.get("status") == "incomplete":
        session_id = context.get("sessionId", "")
        if session_id:
            items = poll_incomplete(session_id, items)

    print(f"  {depart_date}: found {len(items)} unique flights")
    return items


def extract_items_from_buckets(itineraries_data: dict) -> list[dict]:
    """Extract unique flight items from all buckets, deduplicating by ID."""
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
    """Poll the incomplete endpoint until results are complete."""
    url = f"https://{RAPIDAPI_HOST}/web/flights/search-incomplete"

    for attempt in range(max_retries):
        time.sleep(3)
        params = {"sessionId": session_id}
        resp = requests.get(url, headers=HEADERS, params=params)
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

        context = itineraries_data.get("context", {})
        if context.get("status") == "complete":
            print(f"  Results complete after {attempt + 1} polls")
            break

    return current_items


def search_all_flights() -> list[dict]:
    """Search flights for all configured dates."""
    all_items = []
    for date in DEPART_DATES:
        items = search_flights_for_date(date)
        all_items.extend(items)
        if date != DEPART_DATES[-1]:
            time.sleep(2)
    return all_items


def filter_target_flights(items: list[dict]) -> list[dict]:
    """Keep flights departing Apr 30 after 20:00 or anytime on May 1."""
    filtered = []
    for item in items:
        legs = item.get("legs", [])
        if not legs:
            continue

        dep_str = legs[0].get("departure", "")
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


def extract_flight_info(item: dict) -> dict:
    """Extract key fields from a flight item for comparison and display."""
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


def build_notification(changes: list[str], summary: str = "") -> str:
    now = datetime.now(TURKEY_TZ).strftime("%d/%m %H:%M")
    msg = "<b>✈️ SAW → BEG Uçuş Fiyat Takibi</b>\n"
    if summary:
        msg += f"<i>{summary}</i>\n"
    msg += "\n"
    msg += "\n\n".join(changes)
    msg += f"\n\n🕐 Kontrol: {now}"
    return msg


def main():
    print(f"[{datetime.now(TURKEY_TZ).isoformat()}] Checking flights...")

    try:
        all_items = search_all_flights()
    except requests.exceptions.RequestException as e:
        print(f"API request failed: {e}")
        send_telegram(f"⚠️ API hatası: {e}")
        sys.exit(1)

    if not all_items:
        print("No flights returned from API")
        send_telegram("⚠️ API'den uçuş verisi alınamadı.")
        sys.exit(0)

    print(f"Total unique flights found: {len(all_items)}")

    target_flights = filter_target_flights(all_items)
    print(f"Target flights (Apr 30 20:00+ / May 1): {len(target_flights)}")

    if not target_flights:
        print("No flights match the time filter. Showing all flights for reference:")
        for item in all_items:
            info = extract_flight_info(item)
            print(f"  {info['airline']} | {info['departure']} | {info['price_formatted']}")

        target_flights = all_items
        print("Using ALL flights since no evening flights exist yet")

    cache = load_cache()
    changes = []
    new_cache = {}

    for item in target_flights:
        info = extract_flight_info(item)
        flight_key = f"{info['airline']}_{info['departure']}_{info['stops']}"
        old = cache.get(flight_key)

        new_cache[flight_key] = {
            "price": info["price"],
            "price_formatted": info["price_formatted"],
            "airline": info["airline"],
            "departure": info["departure"],
            "arrival": info["arrival"],
            "stops": info["stops"],
            "duration": info["duration"],
            "last_checked": datetime.now(TURKEY_TZ).isoformat(),
        }

        dep_display = format_time(info["departure"])
        arr_display = format_time(info["arrival"])
        stop_text = "Direkt" if info["stops"] == 0 else f"{info['stops']} aktarma"
        dur_text = format_duration(info["duration"])

        if old is None:
            changes.append(
                f"🆕 <b>{info['airline']}</b>\n"
                f"   📅 {dep_display} → {arr_display}\n"
                f"   ⏱ {dur_text} | {stop_text}\n"
                f"   💰 {info['price_formatted']}"
            )
        elif old["price"] != info["price"]:
            old_price = old["price"]
            new_price = info["price"]
            diff = new_price - old_price

            if diff < 0:
                icon = "📉 DÜŞTÜ"
                diff_text = f"-{abs(diff):.0f} TL"
            else:
                icon = "📈 ARTTI"
                diff_text = f"+{diff:.0f} TL"

            changes.append(
                f"{icon}: <b>{info['airline']}</b>\n"
                f"   📅 {dep_display} → {arr_display}\n"
                f"   ⏱ {dur_text} | {stop_text}\n"
                f"   💰 {old['price_formatted']} → {info['price_formatted']} ({diff_text})"
            )

    removed = set(cache.keys()) - set(new_cache.keys())
    for key in removed:
        old = cache[key]
        dep_display = format_time(old["departure"])
        changes.append(
            f"❌ KALDIRILDI: <b>{old['airline']}</b>\n"
            f"   📅 {dep_display} | {old['price_formatted']}"
        )

    notify_state = load_notify_state()
    if not notify_state.get("intro_sent"):
        send_telegram(INTRO_MESSAGE)
        notify_state["intro_sent"] = True
        save_notify_state(notify_state)
        print("Sent one-time intro message to Telegram")

    now = datetime.now(TURKEY_TZ).strftime("%d/%m %H:%M")

    if changes:
        summary = f"{len(target_flights)} uçuş takip ediliyor"
        msg = build_notification(changes, summary)
        print(f"Sending notification with {len(changes)} changes")
        send_telegram(msg)
    else:
        print("No price changes detected")
        send_telegram(
            "<b>✈️ SAW → BEG</b> — Kontrol tamamlandı.\n"
            f"Fiyat / uçuş listesinde <b>değişiklik yok</b>.\n"
            f"<i>{len(target_flights)} uçuş izleniyor.</i>\n\n"
            f"🕐 {now}"
        )

    save_cache(new_cache)
    print("Cache updated successfully")


if __name__ == "__main__":
    main()
