# Crypto markets from Binance (shown on the Markets page)

12 crypto pairs (BTC, ETH, BNB, XRP, SOL, ADA, DOGE, AVAX, LINK, LTC, TRX, DOT against USDT) appear on the Markets page under **Crypto · Binance prices**, with live candles (1, 5 and 15 minutes),
the same chart, countdown and plan locks as the Quotex pairs. The prices are **Binance's public market data**: no account, no API key, no cost.

Code: `backend/app/cryptofeed.py` (the feed), `Engine.feed_display` / `register_display` (storing and charting), `candles.is_display_only` (the marker), `GET /api/platform-admin/crypto` (admin status).
Tests: `backend/tests/test_cryptofeed.py`, `admin/src/lib/markets.test.ts`.

## How it works
- One task inside the server holds **one** combined WebSocket (`<pair>@kline_1m`, `@kline_5m`, `@kline_15m` for every pair) and, at the same time, fills the missing history from the REST klines
  (`/api/v3/klines`, up to 1000 candles per pair and timeframe: about 16 hours of 1-minute, 3.5 days of 5-minute and 10 days of 15-minute candles at the first start; after that only what is missing).
- Binance sends open / high / low / close itself, so nothing is built from ticks: the chart shows Binance's own candles. Closed candles are stored in the normal candle table as `BTCUSDT_bn` etc.
- Hosts: Binance's market-data-only hosts first (`data-stream.binance.vision`, `data-api.binance.vision`), the main ones (`stream.binance.com:9443`, `api.binance.com`) as a fallback.
  `QXS_BINANCE_REST` / `QXS_BINANCE_WS` (comma-separated) replace them. A dropped connection retries with back-off (3 s up to 60 s); a stream silent for 60 s is treated as dead.
- It adds the `websockets` package (see `requirements.txt`).
- Switch it off with `QXS_CRYPTO=off` (default on). With it off, no crypto pair is listed and nothing connects to Binance.

## What these markets are NOT
They are **shown only**. `candles.is_display_only` (symbols ending `_bn`) keeps them out of everything that trades or learns:
no strategy runs on them, no signal, alert, settlement or live session comes from them, the AI advisor, the momentum AI, the Edge Lab, the research pass, the sweeps and the backtest "all pairs" runs leave them out,
extensions are never asked to fill their holes, and they do not count in the Quotex feed-health messages. The chart hides the "market read" panel for them, because its track record is measured on Quotex OTC pairs.

## Before you show it in a paid product
- **Read Binance's terms of use and its market-data terms yourself.** I found that the market-data-only endpoints need no key, but I did **not** find (and did not verify) what Binance's terms say about
  showing this data inside a product people pay for. That is your decision to make, not a technical one. If the terms do not allow it, switch the feed off (`QXS_CRYPTO=off`) or move to a licensed provider
  (Twelve Data lists crypto; its paid tiers are a licence for display).
- Binance refuses some countries on its main hosts; the market-data-only hosts are used first for that reason, but check the server's location. The status page `GET /api/platform-admin/crypto` shows which host is in use and the last error.
- These are Binance's prices, not Quotex's, and crypto trades around the clock: the weekend / closed-market behaviour of the Quotex pairs does not apply.
- Storage grows by about 12 pairs x 3 timeframes (about 21,000 1-minute candles a day for the 12 pairs). There is no clean-up job yet.

## Adding more
`PAIRS` in `cryptofeed.py` is the list (Binance symbol, display name). Forex, stocks and futures need another provider; the same `feed_display` path and a new symbol suffix in `candles.DISPLAY_SUFFIXES`
would carry them (the options and prices I found are in the conversation that built this: Twelve Data, Finnhub; Yahoo's terms exclude paid products).
