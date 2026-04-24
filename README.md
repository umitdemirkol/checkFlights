# checkFlights ✈️

İstanbul Sabiha Gökçen (SAW) → Belgrad (BEG) ve SAW → Tuzla (TZL) **direkt** gidiş uçuş takibi.

## Ne Yapar?

- Tarihler: **30 Nisan** ve **1 Mayıs 2026**
- **Her 2 saatte** kontrol (GitHub Actions cron)
- Her çalışmada **tüm bulunan direkt uçuşları** fiyatlarıyla Telegram'a gönderir
- Fiyat değişimlerini (artış/düşüş) gösterir
- **Google Flights** arama linki
- Kaynak: **Google Flights API** (RapidAPI)

## API

- **Google Flights** (google-flights2.p.rapidapi.com) — aylık 150 istek (ücretsiz plan)
- Her çalışmada 4 istek (2 rota × 2 tarih), günde ~48 istek, ~3 gün yeter

## GitHub Secrets (Environment: UmidoKey)

| Secret | Açıklama |
|--------|----------|
| `RAPIDAPI_KEY` | RapidAPI key |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `TELEGRAM_CHAT_ID` | Telegram grup / chat ID |

## Manuel Çalıştırma

Actions → **Check Flight Prices** → **Run workflow**. İsteğe bağlı **Reset cache** ile önbellek sıfırlanır.
