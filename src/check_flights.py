"""
SAW -> BEG / TZL uçuş takibi.
Birincil: Kiwi.com Cheap Flights (RapidAPI) — rezervasyon linki bookingUrl.
Yedek: Flights Scraper Sky — Kiwi yanıtı hedef tarihlere uymazsa (bilinen API davranışı).
Her çalışmada tüm bulunan direkt uçuşlar Telegram'a gider; fiyat önceki kontrole göre işaretlenir.
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

KIWI_HOST = "kiwi-com-cheap-flights.p.rapidapi.com"
SKY_HOST = "flights-sky.p.rapidapi.com"

TURKEY_TZ = timezone(timedelta(hours=3))

ROUTES = [
    {"from": "SAW", "to": "BEG", "label": "SAW → BEG (Belgrad)", "kiwi_dest_slug": "belgrade-serbia"},
    {"from": "SAW", "to": "TZL", "label": "SAW → TZL (Tuzla)", "kiwi_dest_slug": "tuzla-bosnia-and-herzegovina"},
]

DEPART_DATE_FROM = "2026-04-30"
DEPART_DATE_TO = "2026-05-01"
DEPART_DATES = ["2026-04-30", "2026-05-01"]

TELEGRAM_MAX = 3900

INTRO_MESSAGE = """<b>Uçuş fiyat takibi (Kiwi + yedek Skyscanner)</b>

Rotalar: SAW → BEG, SAW → TZL
Tarih penceresi: 30 Nisan 20:00 sonrası ve 1 Mayıs (yerel saat)
Sadece <b>direkt</b> uçuşlar.

Her saat tam liste gönderilir; mümkünse <b>Kiwi.com</b> rezervasyon linki eklenir."""

HEADERS_KIWI = {
    "x-rapidapi-key": RAPIDAPI_KEY,
    "x-rapidapi-host": KIWI_HOST,
    "Content-Type": "application/json",
}

HEADERS_SKY = {
    "x-rapidapi-key": RAPIDAPI_KEY,
    "x-rapidapi-host": SKY_HOST,
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
    """Telegram 4096 limit; split on newlines."""
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


def in_target_departure_window(dep: datetime) -> bool:
    """30 Nisan 20:00+ veya 1 Mayıs (yerel kalkış saati)."""
    if dep.tzinfo is None:
        dep = dep.replace(tzinfo=TURKEY_TZ)
    d = dep.astimezone(TURKEY_TZ)
    if d.month == 4 and d.day == 30 and d.hour >= 20:
        return True
    if d.month == 5 and d.day == 1:
        return True
    return False


def format_time_dt(d: datetime) -> str:
    months = {4: "Nis", 5: "May"}
    dd = d.astimezone(TURKEY_TZ)
    return f"{dd.day} {months.get(dd.month, '')} {dd.hour:02d}:{dd.minute:02d}"


def format_duration_minutes(mins: int) -> str:
    h, m = divmod(mins, 60)
    return f"{h}s {m}dk" if h else f"{m}dk"


def fallback_kiwi_search_url(route: dict, dep_local: datetime) -> str:
    """Kiwi arama sayfası (derin link yoksa)."""
    dd = dep_local.astimezone(TURKEY_TZ)
    slug = route["kiwi_dest_slug"]
    # Kiwi tarih-saat path biçimi (yaklaşık)
    dt_part = f"{dd.year}-{dd.month:02d}-{dd.day:02d}-{dd.hour:02d}:{dd.minute:02d}"
    return (
        "https://www.kiwi.com/tr/search/tickets/"
        f"sabiha-gokcen-sabiha-tr/{slug}/{dt_part}/no-return"
    )


# --- Kiwi API ---

def search_kiwi_route(route: dict) -> dict:
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
        "limit": "30",
        "fromDate": DEPART_DATE_FROM,
        "toDate": DEPART_DATE_TO,
        "contentProviders": "KIWI",
        "sortBy": "QUALITY",
        "sortOrder": "ASCENDING",
    }
    resp = requests.get(url, headers=HEADERS_KIWI, params=params, timeout=90)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict):
        if data.get("error"):
            raise ValueError(str(data.get("error")))
        if data.get("message") and not data.get("itineraries"):
            raise ValueError(str(data.get("message")))
    return data


def parse_kiwi_itineraries(route: dict, data: dict) -> list[dict]:
    out = []
    for it in data.get("itineraries") or []:
        sector = it.get("sector") or {}
        segs = sector.get("sectorSegments") or []
        if len(segs) != 1:
            continue  # sadece direkt
        seg = segs[0].get("segment") or {}
        if seg.get("type") != "FLIGHT":
            continue

        src = seg.get("source") or {}
        dst = seg.get("destination") or {}
        dep_s = src.get("localTime") or ""
        arr_s = dst.get("localTime") or ""
        if not dep_s:
            continue
        try:
            dep = datetime.fromisoformat(dep_s)
        except ValueError:
            continue
        try:
            arr = datetime.fromisoformat(arr_s) if arr_s else dep
        except ValueError:
            arr = dep

        if not in_target_departure_window(dep):
            continue

        carrier = (seg.get("carrier") or {}).get("name") or "?"
        price_amt = it.get("price", {}).get("amount")
        try:
            price_f = float(price_amt)
        except (TypeError, ValueError):
            price_f = 0.0
        price_fmt = f"{price_f:,.0f} TL".replace(",", ".")

        dur_sec = int(sector.get("duration") or seg.get("duration") or 0)
        dur_min = max(1, dur_sec // 60)

        booking_url = ""
        edges = ((it.get("bookingOptions") or {}).get("edges")) or []
        if edges:
            node = edges[0].get("node") or {}
            bu = node.get("bookingUrl") or ""
            if bu.startswith("http"):
                booking_url = bu
            elif bu.startswith("/"):
                booking_url = "https://www.kiwi.com" + bu
        if not booking_url:
            booking_url = fallback_kiwi_search_url(route, dep)

        fid = it.get("id") or f"{carrier}_{dep_s}"
        out.append({
            "id": fid,
            "source_api": "kiwi",
            "airline": carrier,
            "departure": dep_s,
            "arrival": arr_s,
            "dep_dt": dep,
            "price": price_f,
            "price_formatted": price_fmt,
            "duration_min": dur_min,
            "booking_url": booking_url,
            "route_label": route["label"],
            "from": route["from"],
            "to": route["to"],
        })
    return out


# --- Skyscanner (yedek) ---

def extract_sky_buckets(itineraries_data: dict) -> list[dict]:
    seen = set()
    items = []
    for bucket in itineraries_data.get("buckets", []):
        for item in bucket.get("items", []):
            iid = item.get("id", "")
            if iid and iid not in seen:
                seen.add(iid)
                items.append(item)
    return items


def poll_sky_incomplete(session_id: str, current: list[dict]) -> list[dict]:
    url = f"https://{SKY_HOST}/web/flights/search-incomplete"
    for _ in range(3):
        time.sleep(3)
        r = requests.get(url, headers=HEADERS_SKY, params={"sessionId": session_id}, timeout=60)
        if not r.ok:
            break
        data = r.json()
        if not data.get("status"):
            break
        itd = data.get("data", {}).get("itineraries", {})
        new_items = extract_sky_buckets(itd)
        if new_items:
            current = new_items
        if itd.get("context", {}).get("status") == "complete":
            break
    return current


def search_sky_oneway(origin: str, destination: str, depart_date: str) -> list[dict]:
    url = f"https://{SKY_HOST}/web/flights/search-one-way"
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
    resp = requests.get(url, headers=HEADERS_SKY, params=params, timeout=90)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("status"):
        return []
    itd = data.get("data", {}).get("itineraries", {})
    items = extract_sky_buckets(itd)
    if itd.get("context", {}).get("status") == "incomplete":
        sid = itd.get("context", {}).get("sessionId", "")
        if sid:
            items = poll_sky_incomplete(sid, items)
    return items


def parse_sky_items(route: dict, items: list[dict]) -> list[dict]:
    out = []
    for item in items:
        legs = item.get("legs") or []
        if not legs:
            continue
        leg = legs[0]
        if leg.get("stopCount", 99) != 0:
            continue
        dep_s = leg.get("departure") or ""
        if not dep_s:
            continue
        try:
            dep = datetime.fromisoformat(dep_s)
        except ValueError:
            continue
        if not in_target_departure_window(dep):
            continue
        carriers = (leg.get("carriers") or {}).get("marketing") or []
        airline = " / ".join(c.get("name", "?") for c in carriers) or "?"
        po = item.get("price") or {}
        price_f = float(po.get("raw") or 0)
        price_fmt = po.get("formatted") or f"{price_f:,.0f} TL"
        dur = int(leg.get("durationInMinutes") or 0)
        arr_s = leg.get("arrival") or ""
        # Genel arama linki (Google Flights)
        dep_d = dep.astimezone(TURKEY_TZ)
        q = quote(f"One way {route['from']} to {route['to']} {dep_d.strftime('%Y-%m-%d')}")
        booking_url = f"https://www.google.com/travel/flights?q={q}"

        out.append({
            "id": item.get("id") or f"{airline}_{dep_s}",
            "source_api": "sky",
            "airline": airline,
            "departure": dep_s,
            "arrival": arr_s,
            "dep_dt": dep,
            "price": price_f,
            "price_formatted": price_fmt,
            "duration_min": dur,
            "booking_url": booking_url,
            "route_label": route["label"],
            "from": route["from"],
            "to": route["to"],
        })
    return out


def fetch_route_flights(route: dict) -> tuple[list[dict], str]:
    """
    Önce Kiwi; hedef pencerede sonuç yoksa Skyscanner.
    Dönüş: (flights, notes)
    """
    notes = []
    try:
        raw = search_kiwi_route(route)
        flights = parse_kiwi_itineraries(route, raw)
        if flights:
            notes.append("Kaynak: Kiwi.com")
            return flights, " ".join(notes)
        notes.append("Kiwi sonuç vermedi veya tarih penceresiyle eşleşmedi.")
    except requests.HTTPError as e:
        notes.append(f"Kiwi HTTP hatası: {e}")
    except ValueError as e:
        notes.append(f"Kiwi yanıt hatası: {e}")
    except requests.RequestException as e:
        notes.append(f"Kiwi istek hatası: {e}")

    # Yedek: Skyscanner — tarih başına ayrı istek
    all_sky = []
    for d in DEPART_DATES:
        try:
            items = search_sky_oneway(route["from"], route["to"], d)
            all_sky.extend(items)
        except requests.RequestException as e:
            notes.append(f"Sky {d}: {e}")
        time.sleep(2)
    flights = parse_sky_items(route, all_sky)
    notes.append("Kaynak: Skyscanner (yedek)")
    return flights, " ".join(notes)


def price_delta_note(cache: dict, key: str, price: float) -> str:
    old = cache.get(key)
    if old is None:
        return "<i>(yeni)</i>"
    old_p = old.get("price")
    if old_p is None:
        return ""
    if abs(old_p - price) < 0.5:
        return "<i>(değişiklik yok)</i>"
    diff = price - old_p
    if diff < 0:
        return f"<i>(📉 {diff:,.0f} TL)</i>".replace(",", ".")
    return f"<i>(📈 +{diff:,.0f} TL)</i>".replace(",", ".")


def format_flight_block(idx: int, f: dict, cache: dict, route_prefix: str) -> str:
    key = f"{route_prefix}_{f['airline']}_{f['departure']}"
    dep = format_time_dt(f["dep_dt"])
    arr = ""
    if f.get("arrival"):
        try:
            arr = format_time_dt(datetime.fromisoformat(f["arrival"]))
        except ValueError:
            arr = f.get("arrival", "")[:16]
    dur = format_duration_minutes(f["duration_min"])
    delta = price_delta_note(cache, key, f["price"])
    src = "Kiwi" if f["source_api"] == "kiwi" else "Sky"
    link = f.get("booking_url") or "https://www.kiwi.com/tr/"
    href = link.replace("&", "&amp;")
    return (
        f"{idx}. <b>{f['airline']}</b> <code>[{src}]</code>\n"
        f"   📅 {dep} → {arr} | ⏱ {dur}\n"
        f"   💰 <b>{f['price_formatted']}</b> {delta}\n"
        f'   <a href="{href}">Rezervasyon / detay</a>'
    )


def build_report(sections: list[str], footer: str) -> str:
    msg = "<b>✈️ Saatlik uçuş raporu</b>\n\n"
    msg += "\n\n".join(sections)
    msg += f"\n\n{'—' * 12}\n{footer}"
    return msg


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

    all_sections = []
    total = 0

    for route in ROUTES:
        prefix = f"{route['from']}_{route['to']}"
        try:
            flights, src_note = fetch_route_flights(route)
        except Exception as e:
            all_sections.append(
                f"<b>{route['label']}</b>\n⚠️ Hata: {e}"
            )
            continue

        flights.sort(key=lambda x: (x["dep_dt"], x["price"]))
        total += len(flights)

        lines = [f"<b>{route['label']}</b>", f"<i>{src_note}</i>", f"<i>{len(flights)} direkt uçuş</i>", ""]
        if not flights:
            lines.append("<i>Bu pencerede direkt uçuş bulunamadı.</i>")
        else:
            for i, f in enumerate(flights, 1):
                lines.append(format_flight_block(i, f, cache, prefix))
                key = f"{prefix}_{f['airline']}_{f['departure']}"
                new_cache[key] = {
                    "price": f["price"],
                    "price_formatted": f["price_formatted"],
                    "airline": f["airline"],
                    "departure": f["departure"],
                    "source_api": f["source_api"],
                    "last_checked": datetime.now(TURKEY_TZ).isoformat(),
                }
        all_sections.append("\n".join(lines))

    footer = f"🕐 Kontrol: {now} | Toplam {total} direkt uçuş"
    report = build_report(all_sections, footer)
    send_telegram_long(report)

    save_cache(new_cache)
    print("Done.")


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        msg = f"⚠️ HTTP hatası: {e}"
        print(msg)
        send_telegram(msg)
        sys.exit(1)
