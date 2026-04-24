# checkFlights ✈️

İstanbul Sabiha Gökçen (SAW) → Belgrad (BEG) ve SAW → Tuzla (TZL) **direkt** gidiş uçuş takibi.

## Ne Yapar?

- Tarih penceresi: **30 Nisan 2026 20:00 sonrası** ve **1 Mayıs 2026** (yerel saat)
- **Her saat** kontrol (GitHub Actions cron, UTC saat başı)
- Her çalışmada **tüm bulunan direkt uçuşları** Telegram’a gönderir (fiyat değişmese bile)
- Mümkünse **Kiwi.com** rezervasyon linki (`bookingUrl`); aksi halde **Skyscanner API** ile yedek liste + genel arama linki
- Önceki kontrole göre fiyat satırında **değişiklik yok / artış / düşüş** notu

## API’ler (RapidAPI — aynı key)

- **Kiwi.com Cheap Flights** — birincil; gerçek Kiwi booking URL
- **Flights Scraper Sky** — Kiwi hedef tarihlere uymazsa yedek veri

> Ücretsiz planda aylık istek limitine dikkat; sık cron kota tüketir.

## GitHub Secrets (Environment: UmidoKey)

| Secret | Açıklama |
|--------|----------|
| `RAPIDAPI_KEY` | RapidAPI key (her iki API için) |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `TELEGRAM_CHAT_ID` | Telegram grup / chat ID |

## Manuel Çalıştırma

Actions → **Check Flight Prices** → **Run workflow**. İsteğe bağlı **Reset cache** ile önbellek sıfırlanır.
