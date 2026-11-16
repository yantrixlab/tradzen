"""SQLite access with numbered migrations. Open a database with open_db(path); it migrates it to the latest schema.

Rules: migrations only ever move forward, are idempotent where cheap, and never drop user data.
Legacy databases (created before migrations existed) are adopted: their `signals` table is kept and extended.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

DEFAULT_STRATEGY_KEY = "confluence-5"


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _add_column(conn: sqlite3.Connection, table: str, col: str, decl: str) -> None:
    if col not in _cols(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def m001_foundation(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL, symbol TEXT, tf INTEGER, basis_t INTEGER, target_t INTEGER,
        direction TEXT, score REAL, strength INTEGER, agree INTEGER, factors TEXT, reason TEXT, entry REAL,
        status TEXT, exit REAL, sentiment REAL);

    CREATE TABLE IF NOT EXISTS candles (
        symbol TEXT NOT NULL, tf INTEGER NOT NULL, ts INTEGER NOT NULL,
        o REAL, h REAL, l REAL, c REAL, ticks INTEGER, source TEXT,
        PRIMARY KEY (symbol, tf, ts)) WITHOUT ROWID;

    CREATE TABLE IF NOT EXISTS sentiment (
        symbol TEXT NOT NULL, ts INTEGER NOT NULL, pct REAL, PRIMARY KEY (symbol, ts)) WITHOUT ROWID;

    CREATE TABLE IF NOT EXISTS instruments (
        symbol TEXT PRIMARY KEY, name TEXT, market TEXT, category TEXT, payout REAL, open INTEGER,
        first_seen REAL, last_seen REAL);

    CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT, detail TEXT);

    CREATE TABLE IF NOT EXISTS strategies (
        id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE, name TEXT, kind TEXT, description TEXT,
        status TEXT, created REAL, updated REAL);
    CREATE TABLE IF NOT EXISTS strategy_versions (
        strategy_id INTEGER NOT NULL, version INTEGER NOT NULL, spec TEXT, notes TEXT, created REAL,
        PRIMARY KEY (strategy_id, version));
    CREATE TABLE IF NOT EXISTS strategy_scope (
        strategy_id INTEGER PRIMARY KEY, symbols TEXT, timeframes TEXT, hours TEXT, market TEXT);

    CREATE TABLE IF NOT EXISTS signal_features (
        signal_id INTEGER PRIMARY KEY, features TEXT, hour_utc INTEGER, weekday INTEGER, session TEXT,
        adx REAL, atr_ratio REAL, rsi REAL, bb_pctb REAL, streak INTEGER, trend_align INTEGER);
    CREATE TABLE IF NOT EXISTS signal_outcomes (
        signal_id INTEGER PRIMARY KEY, settled_at REAL, entry REAL, exit REAL, margin_atr REAL, mfe_atr REAL,
        mae_atr REAL, near_miss INTEGER, result TEXT, candle_ticks INTEGER);
    CREATE TABLE IF NOT EXISTS signal_notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, signal_id INTEGER NOT NULL, created REAL, tags TEXT, note TEXT);
    """)
    # legacy signals tables lack these columns
    for col, decl in (("tier", "TEXT"), ("strategy_id", "INTEGER"), ("strategy_version", "INTEGER"),
                      ("mode", "TEXT"), ("expiry_candles", "INTEGER DEFAULT 1")):
        _add_column(conn, "signals", col, decl)
    conn.executescript("""
    CREATE INDEX IF NOT EXISTS ix_signals_pair ON signals(symbol, tf, basis_t);
    CREATE INDEX IF NOT EXISTS ix_signals_status ON signals(status);
    CREATE INDEX IF NOT EXISTS ix_signals_strategy ON signals(strategy_id, created);
    CREATE INDEX IF NOT EXISTS ix_notes_signal ON signal_notes(signal_id);
    CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts);
    """)
    # the default strategy that every existing signal came from
    now = time.time()
    spec = {"engine": "builtin:confluence5",
            "weights": {"bollinger": 30, "rsi_divergence": 25, "stochastic": 20, "cci": 15, "candlestick": 10},
            "gates": {"min_agree": 2, "tier_a_agree": 3, "tier_a_strength": 40, "min_strength": 30, "max_opposing_weight": 25}}
    conn.execute("INSERT OR IGNORE INTO strategies (key, name, kind, description, status, created, updated) VALUES (?,?,?,?,?,?,?)",
                 (DEFAULT_STRATEGY_KEY, "Confluence 5-Factor", "builtin",
                  "Bollinger bounce, RSI divergence, Stochastic cross, CCI reversal and candlestick pattern vote; 2+ factors agree (tier B), 3+ (tier A).",
                  "live", now, now))
    sid = conn.execute("SELECT id FROM strategies WHERE key=?", (DEFAULT_STRATEGY_KEY,)).fetchone()[0]
    conn.execute("INSERT OR IGNORE INTO strategy_versions (strategy_id, version, spec, notes, created) VALUES (?,?,?,?,?)",
                 (sid, 1, json.dumps(spec), "Initial version (ported from the hard-coded engine).", now))
    conn.execute("UPDATE signals SET strategy_id=?, strategy_version=1 WHERE strategy_id IS NULL", (sid,))
    conn.execute("UPDATE signals SET mode='live' WHERE mode IS NULL")


def m002_backtests(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS backtest_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL, finished REAL, label TEXT, strategy_id INTEGER, strategy_version INTEGER,
        spec TEXT, config TEXT, status TEXT, progress REAL, error TEXT, result TEXT, n_trades INTEGER);
    CREATE TABLE IF NOT EXISTS backtest_trades (
        run_id INTEGER NOT NULL, symbol TEXT, tf INTEGER, t INTEGER, dir INTEGER, tier TEXT, strength INTEGER, agree INTEGER, score REAL,
        entry REAL, exit REAL, result TEXT, profit REAL, payout REAL, features TEXT);
    CREATE INDEX IF NOT EXISTS ix_bt_trades_run ON backtest_trades(run_id, t);
    CREATE TABLE IF NOT EXISTS insights (
        id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL, source TEXT, run_id INTEGER, strategy_id INTEGER, dimension TEXT, bucket TEXT,
        n INTEGER, acc REAL, baseline REAL, q_value REAL, validation TEXT, suggestion TEXT, status TEXT);
    CREATE INDEX IF NOT EXISTS ix_insights_strategy ON insights(strategy_id, status);
    """)


def m003_settings(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")


def m004_manual_trades(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS manual_trades (
      id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL NOT NULL, symbol TEXT NOT NULL, tf INTEGER NOT NULL, entry_t INTEGER NOT NULL,
      direction TEXT NOT NULL, grade TEXT, conviction INTEGER, agree INTEGER, against INTEGER, payout REAL,
      status TEXT NOT NULL DEFAULT 'pending', entry REAL, exit REAL, payload TEXT
    );
    CREATE INDEX IF NOT EXISTS ix_manual_trades_key ON manual_trades(symbol, tf, entry_t);
    """)


def m005_sweeps(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS sweep_runs (
      id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL, finished REAL, label TEXT, config TEXT, status TEXT, progress REAL, error TEXT,
      result TEXT, n_candidates INTEGER
    );
    """)


def m006_research(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS research_runs (
      id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL, finished REAL, source TEXT, config TEXT, status TEXT, progress REAL, error TEXT,
      result TEXT, verdict TEXT, best_auc REAL
    );
    """)


def m007_broker_trades(conn: sqlite3.Connection) -> None:
    """The trades the user really placed on the broker (seen by the extension), and a short log of the raw order events for debugging the parser."""
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS broker_trades (
      id TEXT PRIMARY KEY, symbol TEXT, direction TEXT, amount REAL, open_t REAL, expire_t REAL, close_t REAL, open_price REAL, close_price REAL,
      payout REAL, profit REAL, status TEXT NOT NULL DEFAULT 'open', is_demo INTEGER, first_seen REAL, updated REAL
    );
    CREATE INDEX IF NOT EXISTS ix_broker_trades_open ON broker_trades(open_t);
    CREATE TABLE IF NOT EXISTS broker_events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, dir TEXT, name TEXT, payload TEXT);
    """)


def m008_broker_trades_hidden(conn: sqlite3.Connection) -> None:
    """A removed trade is hidden, not deleted: the extension resends recent closed trades whenever the Quotex tab loads, and a hidden row stays hidden then;
    it can also be restored."""
    _add_column(conn, "broker_trades", "hidden", "INTEGER NOT NULL DEFAULT 0")


def m009_guard_alerts(conn: sqlite3.Connection) -> None:
    """The trading guard's alert memory: one row per (day, event) so an alert is never repeated, and the history the Trading guard page shows. silent = it had already
    happened when the guard first saw it, so nobody was alerted."""
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS guard_alerts (
      id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, day TEXT, key TEXT, level TEXT, rule TEXT, title TEXT, message TEXT, silent INTEGER NOT NULL DEFAULT 0,
      UNIQUE (day, key)
    );
    """)


def m010_style(conn: sqlite3.Connection) -> None:
    """'My style': the market context of each of the user's trades (cached) and the signals of the live rule learned from them (settled at the candle close)."""
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS trade_context (trade_id TEXT PRIMARY KEY, ctx TEXT);
    CREATE TABLE IF NOT EXISTS style_signals (
      id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL, symbol TEXT, direction TEXT, entry_t REAL, entry_price REAL, expire_t INTEGER, inc_atr REAL, off_s REAL, payout REAL,
      status TEXT NOT NULL DEFAULT 'pending', exit_price REAL, settled_at REAL, rule TEXT
    );
    CREATE INDEX IF NOT EXISTS ix_style_signals_status ON style_signals(status, symbol);
    """)


def m011_style_step(conn: sqlite3.Connection) -> None:
    """One-step martingale of the live rule's signals: after a lost signal, the same side again on the next candle."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(style_signals)")}
    for name, typ in (("step_status", "TEXT"), ("step_entry", "REAL"), ("step_exit", "REAL"), ("step_expire_t", "INTEGER")):
        if name not in cols:
            conn.execute(f"ALTER TABLE style_signals ADD COLUMN {name} {typ}")


def m012_accounts(conn: sqlite3.Connection) -> None:
    """Multi-user accounts: users with a role (admin / editor / trader), revocable login sessions, extension API tokens and an audit log of admin actions."""
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE COLLATE NOCASE, pw_hash TEXT NOT NULL,
      role TEXT NOT NULL DEFAULT 'trader', plan_id INTEGER, plan_until REAL, active INTEGER NOT NULL DEFAULT 1,
      must_change_pw INTEGER NOT NULL DEFAULT 0, env_admin INTEGER NOT NULL DEFAULT 0, created REAL, last_login REAL
    );
    CREATE TABLE IF NOT EXISTS sessions (
      token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created REAL, expires REAL, last_seen REAL, ip TEXT, ua TEXT
    );
    CREATE INDEX IF NOT EXISTS ix_sessions_user ON sessions(user_id);
    CREATE TABLE IF NOT EXISTS api_tokens (
      id INTEGER PRIMARY KEY AUTOINCREMENT, token_hash TEXT NOT NULL UNIQUE, user_id INTEGER NOT NULL, label TEXT, feeder INTEGER NOT NULL DEFAULT 0,
      created REAL, last_used REAL, revoked INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS ix_api_tokens_user ON api_tokens(user_id);
    CREATE TABLE IF NOT EXISTS audit_log (
      id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, actor TEXT, action TEXT, target TEXT, detail TEXT
    );
    """)


def m013_features(conn: sqlite3.Connection) -> None:
    """Plans (a named set of feature switches and limits) and per-user overrides. The list of features itself lives in code (app/features.py)."""
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS plans (
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE COLLATE NOCASE, description TEXT NOT NULL DEFAULT '',
      features TEXT NOT NULL DEFAULT '{}', is_default INTEGER NOT NULL DEFAULT 0, created REAL
    );
    CREATE TABLE IF NOT EXISTS user_features (
      user_id INTEGER NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY (user_id, key)
    );
    """)


def m014_strategy_owner(conn: sqlite3.Connection) -> None:
    """Strategy ownership and publishing. owner_id NULL = a shared (admin / editor) strategy, otherwise the user who made it. audience (shared strategies only):
    'admin' = still being tested, nobody but staff gets its signals; 'optin' = published, a user adds it to their account; 'everyone' = every user gets its signals.
    A user's own strategy only ever alerts its owner. Backtest runs get an owner too (quotas, privacy)."""
    _add_column(conn, "strategies", "owner_id", "INTEGER")
    _add_column(conn, "strategies", "audience", "TEXT NOT NULL DEFAULT 'admin'")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_strategies_owner ON strategies(owner_id)")
    conn.execute("CREATE TABLE IF NOT EXISTS strategy_subscriptions (user_id INTEGER NOT NULL, strategy_id INTEGER NOT NULL, muted INTEGER NOT NULL DEFAULT 0, created REAL, "
                 "PRIMARY KEY (user_id, strategy_id))")
    _add_column(conn, "backtest_runs", "owner_id", "INTEGER")


def m015_quotex_link(conn: sqlite3.Connection) -> None:
    """The Quotex account a Tradzen account is tied to. qx_hash is a keyed hash of the account number the extension saw (the number itself is never stored), qx_hint the last
    three digits so the person can recognise it, qx_at when it was linked. One Quotex account can belong to one Tradzen account only (unique index)."""
    _add_column(conn, "users", "qx_hash", "TEXT")
    _add_column(conn, "users", "qx_hint", "TEXT")
    _add_column(conn, "users", "qx_at", "REAL")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_users_qx ON users(qx_hash) WHERE qx_hash IS NOT NULL")


def m016_contrib(conn: sqlite3.Connection) -> None:
    """Shared candles: what each account contributed per day (the counters that tell new candles from ones the server already had), the cases where a contribution
    disagreed with a stored candle (kept for the admin to look at, newest 500), and the accounts whose contributions are paused."""
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS candle_contrib (
        day TEXT NOT NULL, uid INTEGER NOT NULL, batches INTEGER NOT NULL DEFAULT 0, received INTEGER NOT NULL DEFAULT 0, new INTEGER NOT NULL DEFAULT 0,
        dup INTEGER NOT NULL DEFAULT 0, conflict INTEGER NOT NULL DEFAULT 0, rejected INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (day, uid)) WITHOUT ROWID;
    CREATE TABLE IF NOT EXISTS candle_disputes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, uid INTEGER, symbol TEXT, tf INTEGER, candle_ts INTEGER, stored TEXT, sent TEXT, stored_source TEXT);
    CREATE TABLE IF NOT EXISTS candle_contrib_block (
        uid INTEGER PRIMARY KEY, reason TEXT, since REAL, auto INTEGER NOT NULL DEFAULT 0);
    """)


def m017_live_feed(conn: sqlite3.Connection) -> None:
    """Live prices from ordinary accounts (per-pair leases): per day and account what was applied / refused / late and how the account's ticks compared with the
    established feed, and the accounts whose live prices are paused."""
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS feed_stats (
        day TEXT NOT NULL, uid INTEGER NOT NULL, requests INTEGER NOT NULL DEFAULT 0, applied INTEGER NOT NULL DEFAULT 0, bad INTEGER NOT NULL DEFAULT 0,
        late INTEGER NOT NULL DEFAULT 0, shadow_ok INTEGER NOT NULL DEFAULT 0, shadow_bad INTEGER NOT NULL DEFAULT 0, dropped INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (day, uid)) WITHOUT ROWID;
    CREATE TABLE IF NOT EXISTS feed_block (
        uid INTEGER PRIMARY KEY, reason TEXT, since REAL, auto INTEGER NOT NULL DEFAULT 0);
    """)


MIGRATIONS = [(1, m001_foundation), (2, m002_backtests), (3, m003_settings), (4, m004_manual_trades), (5, m005_sweeps), (6, m006_research),
              (7, m007_broker_trades), (8, m008_broker_trades_hidden), (9, m009_guard_alerts), (10, m010_style), (11, m011_style_step), (12, m012_accounts), (13, m013_features), (14, m014_strategy_owner),
              (15, m015_quotex_link), (16, m016_contrib), (17, m017_live_feed)]


def schema_version(conn: sqlite3.Connection) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied REAL)")
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return row[0] or 0


def migrate(conn: sqlite3.Connection) -> int:
    current = schema_version(conn)
    for version, fn in MIGRATIONS:
        if version > current:
            with conn:                                   # one transaction per migration
                fn(conn)
                conn.execute("INSERT INTO schema_version (version, applied) VALUES (?,?)", (version, time.time()))
            current = version
    return current


def open_db(path: str | Path = ":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    migrate(conn)
    return conn


# ---------------------------------------------------------------------------------------------- one private database per user
# The hosted version keeps everything that is private to a user (their broker trades and raw order events, trading-guard memory, learned style, settings) in a database file
# of their own (data/users/<id>.db). The tables are the same ones the main database has, so the per-user code is unchanged, and one user can never read another's rows:
# there is nothing to filter. The main database keeps the shared market and the account of the admin (the data that existed before accounts did).
USER_MIGRATIONS = [(3, m003_settings), (7, m007_broker_trades), (8, m008_broker_trades_hidden), (9, m009_guard_alerts), (10, m010_style), (11, m011_style_step)]


def open_user_db(path: str | Path = ":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    current = schema_version(conn)
    for version, fn in USER_MIGRATIONS:
        if version > current:
            with conn:
                fn(conn)
                conn.execute("INSERT INTO schema_version (version, applied) VALUES (?,?)", (version, time.time()))
            current = version
    return conn
