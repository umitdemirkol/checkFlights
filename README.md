# checkFlights ✈️

Istanbul Sabiha Gökçen (SAW) → Belgrade (BEG) uçuş fiyat takip botu.

## Ne Yapar?

- 30 Nisan 2026 20:00'den sonra ve 1 Mayıs 2026 gecesine kadar olan gidiş uçuşlarını takip eder
- Her 4 saatte bir fiyatları kontrol eder (GitHub Actions cron)
- Fiyat değişikliği olduğunda Telegram üzerinden bildirim gönderir
- Yeni uçuş eklenmesi, fiyat artışı/düşüşü ve uçuş kaldırılması durumlarını bildirir

## Kullanılan Servisler

- **Flights Scraper Sky API** (RapidAPI) — Uçuş fiyat verisi
- **Telegram Bot API** — Bildirim
- **GitHub Actions** — Zamanlayıcı (cron) ve çalıştırma ortamı

## GitHub Secrets (Environment: UmidoKey)

| Secret | Açıklama |
|--------|----------|
| `RAPIDAPI_KEY` | RapidAPI API key |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token (@BotFather) |
| `TELEGRAM_CHAT_ID` | Telegram chat ID |

## Manuel Çalıştırma

GitHub repo → Actions → "Check Flight Prices" → "Run workflow" butonu ile manuel tetiklenebilir.
