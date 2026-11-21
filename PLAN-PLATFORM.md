# Plan: P&L calendar, plans with manual payments, and a public chart platform fed by the server

Written 2026-11-21 from four requests: (1) a monthly P&L heat-map calendar on My trades, (2) a chart platform like the reference site with locked / unlocked pairs, (3) plan purchase by UPI or USDT where the user
submits a UTR / transaction ID and the admin approves it, (4) OTC and live-market candles rendered by the SERVER, so nobody sends candles from their own chart.

Nothing below is built yet. Phases are ordered so each one is useful alone; phase 4 is the only one with an outside dependency.

## What already exists (so nothing is rebuilt)
| Need | Already in the code |
|---|---|
| Users, roles, sign-in, CSRF, audit log, default-deny routes | `auth.py`, `routes_auth.py`, rule table in `auth.py` |
| Plans with feature switches, `users.plan_id`, `users.plan_until` | `features.py`, `plans` / `user_features` tables, admin Features / Users pages |
| Candle store, live engine, chart component, market read panel | `engine.py`, `chartdata.py`, `PriceChart.tsx`, `SmartChart.tsx`, `FlowPanel.tsx` |
| Daily P&L, per-pair stats, trade detail | `broker_trades.py`, `GET /api/trades` (`daily`, `by_pair`), `MyTrades.tsx`, `TradeDetail.tsx` |
| Candles sent by users' extensions / live-price leases | `contrib.py`, `leases.py` (to be switched off in phase 4) |
| No self sign-up, no payments, no public chart pages | to build |

## Phase 1 - P&L calendar on My trades (frontend + one small endpoint; about 1-2 days) - BUILT
What the reference shows: range pills (This month, Last month, 3 months, 6 months, Lifetime, Custom), the period total and the selected day's total in the header, a month grid whose cells are green or red.

- **Calendar card** (top of the page, left, wide): month grid Monday-or-Sunday first (setting), each cell = day number, net P&L, trades count; colour intensity by size of the result relative to the month's biggest day; empty days grey; today outlined; months navigable with arrows plus the range pills above. Week totals in a right-hand column. Hover / tap a day = tooltip (trades, wins / losses / draws, win rate, staked, net, best and worst trade, best pair that day). Click a day = the page's trade list filters to that day (uses the existing exact-date filter) and scrolls to it.
- **Summary strip** under the header: period net, return on staked, green days / red days, best day, worst day, longest winning-day streak, average day.
- **"Best pairs" card beside the calendar** (the user's "buy pair section" = the page's existing *By pair* block moved to the top): top 5 pairs by net with trades, win rate, net, a small bar; click = filter by pair. The full table stays at the bottom.
- **Data:** `GET /api/trades` already returns `daily`; add `GET /api/trades/calendar?month=YYYY-MM&account=` returning per-day {trades, won, lost, draw, staked, net, best, worst, top_pair} computed in the user's calendar days (same day rules as the page). Honours Real / Demo / All and the money-hidden eye (amounts shown as dots when hidden).
- **Tests:** day bucketing across midnight and time zones, empty months, demo vs real, hidden-amount mode; admin build + the existing 27 tests.

## Phase 2 - Plans and manual payments (about 3-4 days) - BUILT: see the README entry "Plans and manual payments"
Goal: a person picks a plan, pays by UPI or USDT outside the app, submits the reference, the admin approves, the plan switches on for 30 days.

**Data** (main database):
- `pay_methods`: id, kind (`upi` | `usdt` | other), label, currency, price (per 30 days), instructions text, address / UPI id, QR image path, active, sort order. Admin edits these; the QR images are uploaded by the admin (png / jpg, size-capped, stored under `data/pay/`, served only through a route that checks the file name).
- `pay_orders`: id, user_id, plan_id, method_id, amount, currency, reference (UTR or transaction hash), network (for USDT), note, optional proof image, status (`pending` | `approved` | `rejected` | `cancelled`), reviewer, review note, created, reviewed, plan_from, plan_until.
- **The reference is unique across all orders** (a UTR can only be used once), format-checked (UPI UTR: 12 digits; TRC20 hash: 64 hex; BEP20 / ERC20: 0x + 64 hex), and one pending order per user at a time. Rate limits on submissions.

**User side:** *Plans* page (public pricing section + signed-in page): plan cards with what is unlocked, method tabs (UPI / USDT) showing the admin's QR and instructions, a form for the UTR / transaction ID (and an optional screenshot), the order status and history, "your plan runs until <date>", a reminder 3 days before it ends.

**Admin side:** *Payments* page: pending queue (oldest first, with the user, plan, amount, reference, proof, time since submitted), Approve (extends `plan_until` by 30 days from the later of now and the current end, sets `plan_id`, writes the audit log) / Reject (reason shown to the user), search by reference or user, daily totals, the payment-method editor with QR upload, plan prices, CSV export. Expired plans fall back to the default plan automatically (check on every request, no cron needed).

**Honest limits to decide on:**
- A UTR typed by a user proves nothing by itself: the admin must match it to the bank / wallet statement before approving. A fake or reused number is the usual abuse; the uniqueness rule stops reuse, not invention.
- USDT can be verified automatically later (look the hash up on a public chain explorer: right address, amount, confirmations). UPI cannot, without a payment gateway (Razorpay / Cashfree / PhonePe) that confirms the payment itself. The manual flow is built so a gateway or the chain check can be added as an "auto-approve" step without changing the tables.
- Receiving money makes this a business: terms of service, a refund policy, and the local rules for what you sell (see the last section).

## Phase 3 - The public chart platform (about 4-6 days for the UI on the current feed) - BUILT in the signed-in app (Markets page, server-side locks, open sign-up switch, plans on the sign-in page); NOT built: a pushed (WebSocket / SSE) stream - the page polls once a second -, e-mail verification, a device limit per account, public chart pages without sign-in
The reference site: header with pair, timeframe, a live countdown to the candle close and a LIVE badge; a market sidebar with OTC and live groups where locked pairs show a padlock; a pricing modal.

- **Public site section** (same app, routes `/charts/...`, a landing / pricing page, sign-up and sign-in): reuses `PriceChart` for the candles, the countdown, the zoom and pan, and the market-read panel; no admin menus.
- **Market sidebar:** search, groups (OTC, live currencies, commodities, crypto, indices) from the instruments the server already knows, each pair with its payout and open / closed state; locked pairs open the pricing modal. The free plan gets a small fixed set (the reference leaves two OTC pairs open); the paid plan opens everything. **Locks are enforced on the server** (chart and candle routes refuse a locked pair), not only drawn in the UI.
- **Real-time delivery:** candles are pushed by the server over a WebSocket / SSE stream per open pair (the forming candle updates every tick, the countdown runs from the server clock), instead of the admin's polling.
- **Sign-up:** self sign-up with e-mail verification (or admin-created accounts only; decide), per-account device limit so one paid plan is not shared by many people.
- **Same data for everybody:** every viewer sees the one candle store, so the chart is identical for all users and identical to what the engine and the strategies use.

## Phase 4 - Candles from the server, none from users (the dependency; start with the spike)
Today candles reach the server from (a) the admin's price-feed tab, (b) other users' extensions (`contrib`, `leases`). The goal is a server process that fetches and streams the OTC and live prices itself.

- **Step 0 (already written, waiting for you):** `backend/tools/quotex_spike.py` connects to Quotex with ONE dedicated account through the open-source PyQuotex client and compares its candles with the stored ones: shape, freshness (how many minutes behind), gaps, price agreement, session stability. Its report decides everything below.
- **If the spike is good:** a separate worker container in Coolify (so a crash or a Quotex reconnect never takes the site down) holds the connection, subscribes to every OTC and live pair, builds candles from the ticks with the same rules as the live engine, back-fills history on start, and feeds the engine through the existing feeder path. A watchdog restarts it and raises an alert after N silent seconds; the admin's tab stays as a fallback feed. Then `QXS_CONTRIB=off` and the lease feed off: nobody's extension supplies candles any more.
- **"Exactly the same as Quotex"** is measured, not assumed: a nightly job compares the server's closed candles with the admin's tab feed for the same minutes; the target is identical open / high / low / close on at least 99.9 % of them, and the Data page shows the number.
- **Live markets:** Quotex's own live-pair prices come through the same connection, so they match the Quotex chart. Outside feeds (Twelve Data, Tiingo, Yahoo) are different prices and stay as research data only.
- **Risks, stated plainly:** PyQuotex is an unofficial client; Quotex can ban the account, change the protocol or block the server's address, and its terms may forbid automated access or redistributing its prices. The service must keep working with the fallback feed when that happens. Nothing here should be sold as "official Quotex data".

## Order of work and what I need from you
1. **Phase 1** can start now and ships alone.
2. **Phase 2** next (it unlocks revenue and needs no data work).
3. **Phase 3** on the current feed, so the platform can be tested with real users while phase 4 is decided.
4. **Phase 4** only after the spike report. For the spike I need you to run `quotex_spike.py` with a dedicated account (I never handle the login).

Decisions I need (my recommendation first):
- **Where the platform lives:** same app and domain with public `/charts` pages (one deploy, one login) rather than a second site.
- **Payments now:** manual UTR / hash review as you described; USDT auto-check and a UPI gateway later.
- **Free plan:** two OTC pairs unlocked, the rest locked.
- **Sign-up:** open sign-up with e-mail verification, or accounts created by you only.
- **Plan price and length:** $5 / ₹499 for 30 days as on the reference, or your own.

## Before you take money
Binary-options services and signal sellers are restricted or banned in some countries (including for sellers and for payments in India), reading or redistributing a broker's prices may breach its terms, and taking UPI / crypto payments brings tax and refund duties. Nothing in the app is financial advice. Check the rules that apply to you and your users, and put clear terms, a risk warning and a refund policy on the pricing page before the first sale.
