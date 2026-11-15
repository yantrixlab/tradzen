# Tradzen – Phase 2 plan: Strategy Lab, Loss Analysis & Admin Panel

Status today: live pipeline works (extension → backend → 5-factor engine → settle → cards/sound). Signals and results are stored, but
(1) candles are only in memory, (2) a signal keeps only its factor votes (no indicator values / context), (3) there is exactly one strategy,
hard-coded, (4) the dashboard is a single page. This plan fixes all four.

Honest framing that shapes the design: public indicator strategies on 1–15 min binaries sit near break-even (≈52–54 % needed at 85–92 % payout).
The product value is therefore **measurement and selection** – run many strategies side by side in paper mode, keep only what beats break-even
out of sample, and show why losses happen – not a promise that any one strategy wins.

**Decisions confirmed:** admin UI = React + TypeScript + Vite + Tailwind (served by the backend) · strategy editing = visual builder + JSON tab ·
build order = phases A–C (backend) first, then the admin panel on top.

---------------------------------------------------------------------------------------------------------------------------------------

> **Phase A status: DONE (2026-11-11).** Migrations (`app/db.py`), persisted candles/instruments/sentiment/events, strategies tables seeded with the
> default strategy, per-signal feature snapshot (`app/features.py`), rich outcomes, notes/tags, history query + signal detail + coverage API, shutdown flush.
> Tests: `tests/test_foundation.py` (6) + existing suites. Live DB migrated in place (backup kept in `backend/data/backup-*`).
> Not yet created (arrive with their phase): insights, backtest_runs/trades, alert_policy.

> **Phase B status: DONE (2026-11-11).** Declarative strategy specs (`app/spec.py`), safe expression language (`app/expr.py`, no eval), indicator registry
> (`app/indreg.py`: SMA/EMA/RSI/ATR/CCI/Williams/ROC/Bollinger/Stochastic/MACD/ADX/Supertrend/PSAR/Heikin-Ashi/Donchian), evaluator with gates, tiers and
> filters (`app/evaluator.py`), 16 built-in strategies + consensus meta-strategy (`app/builtins.py`), lifecycle/versions/clone/delete
> (`app/strategy_repo.py`), multi-strategy runner with grouped alerts and auto-mute (`app/engine.py`), strategy API + builder catalog (`/api/strategies*`,
> `/api/spec/catalog`). The default strategy was ported to a spec and matches the legacy engine exactly on 80 random series.
> Built-ins ship in paper mode; Ichimoku, Williams%R+CCI, pivot/Fibonacci and sentiment-contrarian are deferred.
> Tests: `test_strategy_engine.py` (11) + `test_runner.py` (7) + earlier suites.

> **Phase C status: DONE (2026-11-11).** Vectorised evaluator + feature frame with parity tests (`app/vec.py`), backtester with no-look-ahead settlement,
> gap-aware runs, persisted runs/trades, background jobs and time-split reports (`app/backtest.py`, `app/stats_util.py`), clinic: bucket slicing over 18 dimensions,
> Benjamini-Hochberg-corrected findings fitted on older data and validated on the newest, filter suggestions, candidate specs, baseline-vs-candidate verdicts,
> similar-signal lookup, insights lifecycle (`app/clinic.py`); API: `/api/backtests*`, `/api/clinic/{analyze,try,apply}`, `/api/insights*`, `/api/signals/{id}/similar`.
> Tests: `test_vec` (4) + `test_backtest` (8) + `test_clinic` (9). First live run (default strategy, 1m, ~4 h of stored candles per pair): 386 trades, 50.9 % vs 55.6 % break-even
> (too little data to conclude; no significant clinic findings).
> Still to do from section 4/3: parameter sweeps with walk-forward stability view, multi-strategy comparison view, data purge. These arrive with the admin panel (phase D).

> **Phase D status: DONE (2026-11-11).** React + TypeScript + Vite + Tailwind admin panel in `admin/`, built into `backend/static/admin`, served at `/admin`.
> Pages: Overview, Signal history (+ inspector drawer), Strategies list/detail, Strategy builder (visual + JSON, live validation, preview), Backtest lab (+ run detail),
> Clinic (analysis, findings, try/apply, saved insights), Data, Alerts (read-only policy view), System. New API: `/api/equity`, `/api/system`, strategy version fetch.
> Deferred: server-side alert policy editor (alerts are configured per browser in the extension), parameter sweeps/walk-forward stability, multi-strategy compare view, data purge,
> admin authentication (needed before any VPS exposure).

## 1. Data foundation (everything else depends on it)

SQLite now (WAL, one file, easy backup); schema versioned with numbered migrations so a later move to Postgres/Timescale on the VPS is mechanical.

| Table | Purpose / key columns |
|---|---|
| `instruments` | symbol, name, market (OTC/LIVE), category, payout, open, first/last seen |
| `candles` | symbol, tf, ts, o, h, l, c, ticks, source (tick/history) – **persisted**, PK (symbol, tf, ts). Dedupe by ticks count |
| `sentiment` | symbol, ts, pct (trader sentiment feed `depth/change`) – sampled 1/min |
| `strategies` | id, key, name, kind (builtin/custom), description, status (draft/paper/live/paused), created/updated |
| `strategy_versions` | strategy_id, version, spec JSON, notes, created – **immutable**; edits create a new version |
| `strategy_scope` | strategy_id, pairs filter, timeframes, hours filter, market (OTC/LIVE) |
| `signals` | existing columns + strategy_id, strategy_version, mode (paper/live), expiry_candles |
| `signal_features` | signal_id, JSON snapshot of ~40 context features (below) + extracted columns for fast grouping |
| `signal_outcomes` | signal_id, entry/exit, margin_atr (how far past entry, in ATR), mfe/mae inside the candle, near_miss flag, result |
| `signal_notes` | signal_id, tags[], note – you tag a loss ("news spike", "against trend") |
| `backtest_runs` / `backtest_trades` | run config + results; trades have the same shape as live signals + features |
| `insights` | auto-generated findings from the clinic (bucket, lift, n, CI, status: new/tested/applied/dismissed) |
| `alert_policy` | which strategies/tiers/pairs alert, min-edge gate, quiet hours |
| `events` | feed log, tick gaps, socket events, errors (replaces the in-memory list) |

**Feature snapshot stored with every signal** (so losses can be explained later, and so the same feature table feeds ML in phase 5):
RSI(14), RSI(2), Stoch K/D, CCI, MACD/hist, BB %b + bandwidth percentile, ATR(14) + ATR vs 50-bar mean (volatility regime), ADX/DI (trend vs range),
EMA9/21/50 slope & stack order, distance to 20-bar high/low, candle body/wick ratios, last 5 candle colours + streak length, hour (broker/local),
weekday, session (Asia/EU/US), sentiment %, payout, tick rate, pair, tf, direction, tier, strategy votes, time since last signal on the pair,
signals on other pairs in the same minute (correlation/crowding).

**Outcome enrichment**: besides win/loss store margin in ATR units and whether price crossed entry inside the candle but closed wrong (near miss),
so "unlucky" and "structurally wrong" losses are separated.

## 2. Strategy engine v2 (declarative, no code eval)

A strategy is a **spec** (JSON) interpreted by one evaluator, so UI-built and built-in strategies run identically and can be versioned/diffed:

```json
{ "timeframes": [60, 300], "indicators": [
    {"id": "bb", "type": "bollinger", "n": 20, "k": 2}, {"id": "rsi", "type": "rsi", "n": 14}, {"id": "ema", "type": "ema", "n": 200} ],
  "factors": [
    {"name": "BB bounce", "weight": 30, "call": "low <= bb.lower", "put": "high >= bb.upper"},
    {"name": "RSI extreme", "weight": 25, "call": "rsi < 30", "put": "rsi > 70"} ],
  "gates": {"min_agree": 2, "tier_a": {"agree": 3, "strength": 40}, "max_opposing_weight": 25, "min_strength": 30},
  "filters": [ {"type": "adx_max", "value": 30}, {"type": "hours", "exclude": ["21:00-23:00"]}, {"type": "atr_min_ratio", "value": 0.6} ],
  "expiry_candles": 1 }
```
* Indicator registry (cached per symbol/tf/candle so 20 strategies cost ~1 indicator pass): SMA/EMA/WMA, Bollinger, Keltner, Donchian, RSI, Stochastic,
  StochRSI, CCI, Williams %R, MACD, ADX/DI, ATR, Supertrend, Parabolic SAR, Heikin-Ashi, Ichimoku, pivots, swing S/R, candle patterns, streaks, sentiment.
* Condition grammar limited to a safe expression set: comparisons, `cross_above/below`, `touch`, `highest/lowest(n)`, `divergence`, `streak(n, colour)`,
  `pattern(name)`, boolean and/or/not. Parsed to an AST – never `eval`.
* Output of every strategy is the same object as today (direction, score, strength, agree, tier, factor votes) + feature snapshot.
* **Lifecycle**: `draft` (editing, backtest only) → `paper` (live signals recorded & settled, no alerts) → `live` (alerts) → `paused`. New or edited strategies
  always start in paper. Promote with one click once the criteria you set are met (e.g. ≥ 100 settled, lower 95 % bound > break-even).
* **Multi-strategy runner**: every candle close evaluates all active strategies × streaming pairs × timeframes. Each signal carries strategy + version.
  Optional **consensus** meta-strategy: alert only when ≥ k chosen strategies agree on the same direction.
* **Auto-mute**: a live strategy whose upper 95 % confidence bound falls below break-even over its last 100 settled signals, or over its last 500 (a long record catches a strategy that is slowly but clearly losing: over 100 signals chance can hide that), stops alerting automatically. It keeps recording and settling signals while muted and un-mutes by itself when its record no longer shows it below break-even.
* Parity test: the current engine is ported to a spec and must reproduce today's signals exactly on stored candles.

### Built-in strategy library (worldwide-used techniques; each ships as an editable, clonable spec)
| # | Strategy | Idea | Usual timeframe |
|---|---|---|---|
| 1 | **Confluence 5-Factor** (current default) | BB + RSI divergence + Stoch + CCI + candle, ≥2–3 agree | 1–5m |
| 2 | Bollinger Reversion | close back inside band after outside close / touch | 1–5m |
| 3 | RSI Extremes | RSI 14 (30/70) exit signals; variant Connors RSI(2) 10/90 with 200-SMA trend filter | 1–15m |
| 4 | Stochastic Reversal | %K/%D cross in 20/80 zones | 1–5m |
| 5 | MACD Cross | signal-line cross + histogram flip, optional zero-line filter | 5–15m |
| 6 | EMA Cross Trend | EMA 9/21 cross with EMA 50 slope filter | 5–15m |
| 7 | Supertrend Flip | Supertrend(10,3) colour change | 5–15m |
| 8 | Parabolic SAR + EMA | SAR flip in the direction of EMA 200 | 5–15m |
| 9 | ADX/DI Trend Continuation | ADX > 25 and DI cross, pullback entry | 5–15m |
| 10 | Heikin-Ashi Continuation | 2–3 same-colour HA candles without opposite wick | 1–5m |
| 11 | Engulfing / Pin Bar at S/R | reversal candle at swing/pivot level | 5–15m |
| 12 | Donchian / Range Breakout | close beyond 20-bar high/low with volatility filter | 5–15m |
| 13 | Bollinger Squeeze Breakout | low bandwidth percentile then expansion direction | 5–15m |
| 14 | Williams %R + CCI Reversal | double oscillator extreme + turn | 1–5m |
| 15 | Ichimoku Tenkan/Kijun + Cloud | cross above/below cloud | 5–15m |
| 16 | Pivot / Fibonacci Bounce | daily pivots, 38.2/61.8 retracement bounce | 5–15m |
| 17 | Trend Pullback (3-confirmation) | EMA trend + RSI pullback + Stoch turn | 5–15m |
| 18 | Candle Streak Reversal *(hypothesis)* | after N same-colour candles bet the opposite – popular for OTC; to be tested, not assumed | 1m |
| 19 | Sentiment Contrarian *(hypothesis)* | fade extreme trader-sentiment % from `depth/change` | 1–5m |
| 20 | Consensus (meta) | ≥ k selected strategies agree | any |

First release: #1–#13 + #17 + #20 (the rest follow once the indicator registry has them). None is presumed profitable; all are measured identically.

## 3. Loss analysis ("Clinic")

Goal: answer *"in which situations do we lose, and what should we change"* with statistics, not anecdotes.
1. **Slicing**: per strategy/version, win-rate by pair, tf, hour × weekday heatmap, session, direction (the screenshot shows mostly PUTs – is that a
   bias or a trending market?), tier, factor combination, ADX regime (trend/range), volatility regime (ATR percentile), BB position, streak length,
   sentiment bucket, payout bucket, time since previous signal. Each cell shows n, win %, 95 % interval, lift vs the strategy's baseline.
2. **Findings list** (`insights`): cells with n ≥ 30 and a statistically credible gap (Wilson bound vs baseline, false-discovery-rate adjusted because
   we are checking hundreds of cells). Each finding states the hypothesis ("PUT signals with ADX > 30 win 41 % (n=52) vs 57 % overall") and a suggested
   change expressed as a **filter** or **parameter edit**.
3. **Try it**: "Apply as draft" creates a new strategy version with the filter, runs a **time-split backtest** (fit on the older 70 %, verify on the newest
   30 %) and shows before/after. Only improvements that hold on the verification slice may be promoted; the UI labels the rest "likely noise".
4. **Single-loss inspector**: open any losing signal → candles around it (chart), every feature value vs the strategy's typical winner, near-miss flag,
   the nearest similar past signals and how they ended, and a tag/note box ("news", "against trend") feeding the notes table.
5. Later (phase 5): LightGBM on the same feature table with SHAP to find interactions humans would not slice, producing calibrated win probabilities
   usable as an "AI filter" that can veto signals.

## 4. Backtester / walk-forward

* Same evaluator code as live, replaying stored candles (any pair/tf/date range); no look-ahead (entry = next candle open, settlement = its close).
* Reports: n, win %, 95 % interval, break-even, expectancy per 1 unit stake using each pair's payout, profit factor, max drawdown (units), longest losing streak,
  equity curve, by-bucket tables (same as Clinic), cost of the filters.
* Parameter sweeps limited to small grids with **walk-forward** splits and a "stability" view (neighbouring parameter values must also work) to discourage overfit.
* Side-by-side comparison of any strategies on identical data; paper-trading results shown next to backtest results so divergence is visible.
* Data volume: backtests are only as good as stored history – plan to keep ≥ 30 days; scrolling the Quotex chart back preloads more.

## 5. Admin panel (web app, served by the backend on localhost; auth added before any VPS exposure)

Stack proposal: **React + TypeScript + Vite + Tailwind**, TanStack Table/Query, a charting library for equity/heatmaps (uPlot or Recharts), Monaco-lite JSON editor.
Built to static files and served by FastAPI, so the run command stays `start.ps1`. Dark theme consistent with the alert cards.

| Page | Content |
|---|---|
| **Overview** | feed health (ticks/s, last tick age, gaps), streaming pairs, today's signals/results, strategy leaderboard (paper + live), equity curve, open signals |
| **Live signals** | current page (pair picker, chart, analysis, recent) with strategy selector and per-strategy results |
| **Signal history** | filterable table (strategy, pair, tf, tier, result, date/hour), CSV export, detail drawer with chart + feature snapshot + outcome + notes/tags |
| **Strategies** | list with status/lifecycle, live stats, version history; detail page: description, **spec builder** (indicators, factors with weights, gates, filters, scope) + raw JSON tab with validation; actions: Clone built-in, New version, Activate/Pause, Backtest, Compare, Promote to live, Delete custom |
| **Clinic** | loss analysis (heatmaps, buckets, findings with *Apply as draft*), single-loss inspector, tags |
| **Backtest lab** | run configuration, results, walk-forward, comparison, "promote" |
| **Data** | instruments + payouts, candle coverage and gaps per pair/tf, history import (CSV), export/backup, purge |
| **Alerts** | policy: which strategies/tiers/pairs alert, min-edge gate, quiet hours, sound pack, Telegram (later); extension popup keeps device-local prefs |
| **System** | backend/extension status, DB size, logs/feed events, migrations, update checks |

API (FastAPI, JSON): `/api/strategies` (CRUD, versions, activate, backtest), `/api/signals` (query, detail, notes), `/api/clinic` (slices, findings, apply),
`/api/backtests`, `/api/data`, `/api/alerts`, `/api/system`; existing `/ingest` and `/api/events` unchanged. OpenAPI docs at `/docs`.

## 6. Delivery order

| Phase | Deliverable | Notes |
|---|---|---|
| **A. Data foundation** | migrations; persisted candles; `signals` + `signal_features` + `signal_outcomes`; feature snapshot at signal time | ~1 day; starts collecting the data every later phase needs, so do first |
| **B. Strategy engine v2** | spec format, indicator registry, evaluator, port of the current strategy (parity test), built-ins #1–#13,#17, lifecycle, multi-strategy live runner, auto-mute | live alerts keep working throughout |
| **C. Backtester + Clinic backend** | replay engine, stats, bucket analysis, findings, time-split verification | CLI + API first |
| **D. Admin panel** | React app: Overview, Signals, Strategies (builder), Clinic, Backtest, Data, Alerts, System | built page by page; Strategies + Clinic first |
| **E. ML assist** | LightGBM + SHAP + probability calibration; optional Kronos feature; "AI filter" strategy | needs a few thousand settled signals |
| **F. Hardening / VPS** | admin auth, HTTPS, Docker Compose, backups, Telegram alerts | only when you decide to run it remotely |

Each phase ends with tests (extend the existing backend/extension suites) and a quick live check on your running setup.

## 7. Risks and guard-rails
* **Multiple-comparison trap**: testing 20 strategies × many filters will produce winners by luck. Mitigations: paper-trade before alerting, out-of-sample verification,
  FDR-adjusted findings, minimum n, show both backtest and live-paper results.
* **OTC prices are broker-generated**: backtests on OTC history measure the broker's generator, which can change without notice; keep a rolling window and watch for drift.
* **Data gaps** (tab hidden, feed pauses): gap-flagged candles are excluded from backtests exactly as in live.
* **Strategy editing safety**: specs are data validated against a schema; no arbitrary code; every change is a new immutable version, so old results stay attributable.
* **Expectations**: a tier/strategy is shown as "above break-even" only when the lower confidence bound clears the payout-implied break-even; otherwise the UI says so.
