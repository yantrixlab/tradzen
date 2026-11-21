# The server-side price feed (phase 4 of PLAN-PLATFORM.md)

Goal: candles and live prices come from **the server logging in to Quotex itself**, not from users' charts. The pieces are built and tested; the connection to Quotex is **not verified**
until you have run the self-check below with a real account. Do these steps in order.

## What it is
`backend/app/feedworker.py` is a small separate process. It logs in with one dedicated Quotex account (through the open-source PyQuotex client, `backend/app/pyquotex_adapter.py`), follows the
open pairs, and sends their prices and recent history to the Tradzen server exactly like the admin's Quotex tab does (`POST /ingest` with a price-feed token). Everything after that (candles,
signals, the Markets page) is the code that already runs. If it crashes or Quotex drops it, it reconnects with back-off; the server keeps working from the admin's tab meanwhile.

## Honest limits
- PyQuotex is an **unofficial** client. Quotex can ban the account, change its protocol, or block the server's address, and its terms may forbid automated access or redistributing its prices.
  Use a **dedicated account**, never your main one, and keep the admin's tab feed as the fallback.
- `pyquotex_adapter.py` was written from PyQuotex's documentation, which does not say what several calls return. That is what `--selfcheck` is for. **Do not start the worker for real until the
  self-check output looks right** (send it to me and I will fix the parsers from facts).
- "The same as Quotex" has to be measured: run `backend/tools/quotex_spike.py` (compares Quotex's candles with the stored ones) before and after switching.

## 1. Try it locally first (no Quotex needed)
```bash
cd backend
QXS_FEED_TOKEN=<a feeder token of a LOCAL test server> .venv/Scripts/python -m app.feedworker --adapter fake --url http://127.0.0.1:8787
```
The fake adapter streams a random walk for four pairs. On the Data page -> *Live feed* you should see a feed worker, alive, with 4 pairs.

## 2. Self-check against Quotex (you run it; it prints no credentials)
Use a separate virtualenv with Python 3.12+, **read the PyQuotex code**, install it, then:
```bash
set QX_EMAIL=...        (PowerShell: $env:QX_EMAIL = "...")
set QX_PASSWORD=...
python -m app.feedworker --adapter pyquotex --selfcheck --pairs EURUSD_otc
```
It connects once and prints what the library returned for the asset list, payouts, candles and prices, how many prices arrived in 12 seconds and how old the newest candle is. Send me that output.

## 3. Deploy it in Coolify (second application, same repository)
1. Make a **price-feed token**: sign in to Tradzen as admin -> *My account* -> create a token with *Price feed* ticked. Copy it (shown once).
2. Coolify -> your project -> **+ New Resource** -> the same GitHub repository, branch `main`, **Build Pack: Dockerfile**, Dockerfile location `/Dockerfile.feed`. No domain and no exposed port.
3. **Build arguments:** `PYQUOTEX_SPEC` = `git+https://github.com/cleitonleonel/pyquotex.git@<the full commit hash you reviewed>`.
4. **Environment variables (runtime):** `QXS_URL` = your Tradzen address (`https://...`), `QXS_FEED_TOKEN` = the token, `QX_EMAIL`, `QX_PASSWORD` = the dedicated account.
   Optional: `QXS_FEED_PAIRS` (comma separated, empty = every open pair), `QXS_FEED_MAX_PAIRS` (default 60).
5. Deploy. The container is healthy when the worker has a connection and prices are arriving (its health file, checked every 30 s).

## 4. Check it, then close the other senders
- Data -> *Live feed* -> **Candle sources**: the feed worker should show *alive* with the pairs it follows, and *Streaming now* should count them under "server feed".
- Untick **This browser is the price feed** in your own extension (or leave the tab closed), so two feeders do not send the same pairs.
- When *Ready to switch off* says yes, switch **Candles from ordinary accounts** and **Live prices from ordinary accounts** OFF. Extensions are told "off" and stop sending; the choice survives restarts.
- Run `quotex_spike.py` again and compare: the report should show no missing minutes before the running candle.

## If something is wrong
| Symptom | Likely cause |
|---|---|
| Container unhealthy | Wrong QX_EMAIL / QX_PASSWORD, Quotex blocked the address, or the parsers need adjusting: read the container log (`session ended: ...`) |
| Worker alive but no pairs | The asset list parser did not recognise the library's answer: run `--selfcheck` and send the output |
| Prices stop every few minutes | The silence watchdog reconnects after 45 s without a price; frequent reconnects mean Quotex is dropping the session |
| Both the tab and the worker feed the same pair | Untick "This browser is the price feed" |
