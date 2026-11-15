"""Moving the data you already collected (candles, signal history, strategies, backtests, your own trades, the AI's files) onto a new server.

  python -m app.seed make  [--db data/signals.db] [--out ../seed/tradzen-seed.tar.gz]
      Takes a consistent snapshot of the database (the server may keep running) plus the AI / Edge Lab files and packs them into one archive.
  QXS_SEED=/seed/tradzen-seed.tar.gz  (set by docker-compose)
      On the FIRST start of a server, when no database exists yet, the archive is unpacked as the server's database. After that it is ignored.
  python -m app.seed merge <archive-or-.db> [--db data/signals.db]
      Later, adds the market data (candles, sentiment, instruments) of another database to a running server's. Rows the server already has are kept;
      accounts, strategies and signals are never touched, so it is safe on a server that already has users.

The archive holds one database, so everything in it keeps its ids: that is why it can only start a server, not be mixed into one that has users and strategies.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
import time
from pathlib import Path

from . import db as dbmod

FORMAT = 1
DB_NAME = "signals.db"
FILE_DIRS = ("ai", "edgelab", "ideas")          # what the background jobs wrote next to the database (models, reports); backups and logs are not part of it
MAX_BYTES = 8 * 1024 ** 3
COUNT_TABLES = ("candles", "sentiment", "instruments", "signals", "strategies", "backtest_runs", "broker_trades")


class SeedError(Exception):
    pass


def latest_schema() -> int:
    return max(v for v, _ in dbmod.MIGRATIONS)


def _snapshot(db_path: Path, dst: Path) -> None:
    src = sqlite3.connect(str(db_path), timeout=60)
    out = sqlite3.connect(str(dst))
    try:
        src.backup(out)
    finally:
        out.close()
        src.close()


def _counts(path: Path) -> dict:
    c = sqlite3.connect(str(path))
    try:
        have = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        out = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in COUNT_TABLES if t in have}
        out["schema_version"] = c.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] if "schema_version" in have else 0
        return out
    finally:
        c.close()


def make_seed(db_path: str | Path, out_path: str | Path) -> dict:
    """A consistent snapshot of the database (+ the job files) in one .tar.gz. Returns what is in it."""
    db_path, out_path = Path(db_path), Path(out_path)
    if not db_path.is_file():
        raise SeedError(f"no database at {db_path}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / DB_NAME
        _snapshot(db_path, snap)
        c = sqlite3.connect(str(snap))
        try:
            check = c.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            c.close()
        if check != "ok":
            raise SeedError(f"the database failed its integrity check: {check}")
        info = {"format": FORMAT, "created": time.time(), "counts": _counts(snap), "files": []}
        files = []
        for sub in FILE_DIRS:
            base = db_path.parent / sub
            if base.is_dir():
                files += [(f, f"files/{sub}/{f.relative_to(base).as_posix()}") for f in sorted(base.rglob("*")) if f.is_file()]
        info["files"] = [name for _, name in files]
        manifest = Path(tmp) / "seed.json"
        manifest.write_text(json.dumps(info, indent=2), encoding="utf-8")
        tmp_out = out_path.with_name(out_path.name + ".part")
        with tarfile.open(tmp_out, "w:gz", compresslevel=6) as tar:
            tar.add(manifest, arcname="seed.json")
            tar.add(snap, arcname=DB_NAME)
            for f, name in files:
                tar.add(f, arcname=name)
        os.replace(tmp_out, out_path)
    info["bytes"] = out_path.stat().st_size
    return info


def _safe_members(tar: tarfile.TarFile) -> list[tarfile.TarInfo]:
    """Only the files a seed is made of, as plain files with plain relative names: nothing that could write outside the data folder."""
    out, total = [], 0
    for m in tar.getmembers():
        parts = Path(m.name).parts
        ok = m.isfile() and not Path(m.name).is_absolute() and ".." not in parts and (
            m.name in ("seed.json", DB_NAME) or (len(parts) >= 3 and parts[0] == "files" and parts[1] in FILE_DIRS))
        if not ok:
            raise SeedError(f"the seed archive holds something unexpected: {m.name!r}")
        total += m.size
        out.append(m)
    if total > MAX_BYTES:
        raise SeedError("the seed archive is too large")
    return out


def _open_source(seed: str | Path, tmp: Path) -> tuple[Path, dict, tarfile.TarFile | None]:
    """The database inside a seed (archive or plain .db) as a file, with its manifest."""
    seed = Path(seed)
    if not seed.is_file():
        raise SeedError(f"seed file not found: {seed}")
    if tarfile.is_tarfile(seed):
        tar = tarfile.open(seed, "r:*")
        members = {m.name: m for m in _safe_members(tar)}
        if DB_NAME not in members or "seed.json" not in members:
            tar.close()
            raise SeedError("the archive is not a Tradzen seed (no signals.db / seed.json)")
        info = json.loads(tar.extractfile(members["seed.json"]).read().decode("utf-8"))
        if info.get("format") != FORMAT:
            tar.close()
            raise SeedError(f"unsupported seed format {info.get('format')!r}")
        dst = tmp / DB_NAME
        with tar.extractfile(members[DB_NAME]) as r, open(dst, "wb") as w:
            shutil.copyfileobj(r, w, 1024 * 1024)
        return dst, info, tar
    return seed, {"format": FORMAT, "files": []}, None


def _check_db(path: Path) -> dict:
    try:
        counts = _counts(path)
    except sqlite3.DatabaseError:                         # random bytes, a truncated upload, ...
        raise SeedError("that file is not a Tradzen seed or database")
    if counts["schema_version"] > latest_schema():
        raise SeedError(f"the seed comes from a newer version of the app (schema {counts['schema_version']}, this server knows {latest_schema()}): update the server first")
    if counts.get("candles") is None:
        raise SeedError("that file is not a Tradzen database")
    return counts


def inspect_seed(seed: str | Path) -> dict:
    """What is inside a seed (archive or .db), without loading it: row counts, schema version, creation time, number of job files, size."""
    seed = Path(seed)
    tmp = Path(tempfile.mkdtemp(dir=seed.parent, prefix=".seed-"))
    tar = None
    try:
        src, info, tar = _open_source(seed, tmp)
        counts = _check_db(src)
        return {"counts": counts, "created": info.get("created"), "files": len(info.get("files", [])), "bytes": seed.stat().st_size}
    finally:
        if tar is not None:
            tar.close()
        shutil.rmtree(tmp, ignore_errors=True)


def is_fresh(db_path: str | Path) -> bool:
    """Could the whole database be replaced without losing anyone's accounts? True when there is no database, or it has no users besides the admin from the .env file
    and nobody has a private database."""
    db_path = Path(db_path)
    if not db_path.exists():
        return True
    users_dir = db_path.parent / "users"
    if users_dir.is_dir() and any(users_dir.glob("*.db")):
        return False
    c = sqlite3.connect(str(db_path), timeout=30)
    try:
        has = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'").fetchone()
        return not has or c.execute("SELECT COUNT(*) FROM users").fetchone()[0] <= 1
    finally:
        c.close()


PENDING_NAME = "pending.tar.gz"


def apply_pending(db_path: str | Path) -> dict | None:
    """Start-up hook for a seed uploaded through the admin page (data/seed/pending.tar.gz). If the server is still 'fresh' the old database is set aside (never deleted:
    signals.db.before-seed-<time>) and the seed becomes the database; otherwise the file stays where it is and nothing changes. Returns what was loaded."""
    db_path = Path(db_path)
    pending = db_path.parent / "seed" / PENDING_NAME
    if not pending.is_file() or not is_fresh(db_path):
        return None
    base = time.strftime("%Y%m%d-%H%M%S")
    stamp, n = base, 1
    while db_path.with_name(f"{db_path.name}.before-seed-{stamp}").exists() or (pending.parent / f"loaded-{stamp}.tar.gz").exists():     # never overwrite an earlier one
        n += 1
        stamp = f"{base}-{n}"
    moved = []
    for suffix in ("", "-wal", "-shm"):
        f = db_path.with_name(db_path.name + suffix)
        if f.exists():
            target = db_path.with_name(f"{db_path.name}.before-seed-{stamp}{suffix}")
            os.replace(f, target)
            moved.append((f, target))
    try:
        res = apply_seed(pending, db_path)
    except Exception:
        for f, target in moved:                              # the seed could not be loaded: put the old database back exactly as it was
            os.replace(target, f)
        raise
    os.replace(pending, pending.with_name(f"loaded-{stamp}.tar.gz"))
    return res


def apply_seed(seed: str | Path, db_path: str | Path) -> dict | None:
    """First start only: if there is no database yet, unpack the seed as the database (and its files next to it). Returns what was loaded, None if a database exists."""
    db_path = Path(db_path)
    if db_path.exists():
        return None
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=db_path.parent, prefix=".seed-"))
    tar = None
    try:
        src, info, tar = _open_source(seed, tmp)
        counts = _check_db(src)
        if tar is not None:
            for m in _safe_members(tar):
                if m.name.startswith("files/"):
                    target = db_path.parent / Path(*Path(m.name).parts[1:])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with tar.extractfile(m) as r, open(target, "wb") as w:
                        shutil.copyfileobj(r, w, 1024 * 1024)
        shutil.copyfile(src, db_path.with_name(db_path.name + ".seed-part"))
        os.replace(db_path.with_name(db_path.name + ".seed-part"), db_path)
        return {"counts": counts, "files": len(info.get("files", [])), "created": info.get("created")}
    finally:
        if tar is not None:
            tar.close()
        shutil.rmtree(tmp, ignore_errors=True)


def merge_market(seed: str | Path, db_path: str | Path, progress=None) -> dict:
    """Add the candles, sentiment and instruments of another database to this one. What this server already has is kept. Works in small steps (one pair at a time),
    so a running server is never blocked for long, but stopping it first is still the safest."""
    db_path = Path(db_path)
    if not db_path.is_file():
        raise SeedError(f"no database at {db_path}")
    tmp = Path(tempfile.mkdtemp(dir=db_path.parent, prefix=".seed-"))
    tar = None
    try:
        src, _info, tar = _open_source(seed, tmp)
        _check_db(src)
        conn = sqlite3.connect(str(db_path), timeout=120)
        conn.execute("PRAGMA busy_timeout=120000")
        conn.execute("ATTACH DATABASE ? AS seed", (str(src),))
        added = {"candles": 0, "sentiment": 0, "instruments": 0}
        try:
            pairs = conn.execute("SELECT DISTINCT symbol, tf FROM seed.candles ORDER BY symbol, tf").fetchall()
            for i, (sym, tf) in enumerate(pairs):
                with conn:
                    cur = conn.execute("INSERT OR IGNORE INTO main.candles (symbol, tf, ts, o, h, l, c, ticks, source) "
                                       "SELECT symbol, tf, ts, o, h, l, c, ticks, source FROM seed.candles WHERE symbol=? AND tf=?", (sym, tf))
                    added["candles"] += cur.rowcount
                if progress:
                    progress(i + 1, len(pairs), sym, tf)
            for (sym,) in conn.execute("SELECT DISTINCT symbol FROM seed.sentiment").fetchall():
                with conn:
                    added["sentiment"] += conn.execute("INSERT OR IGNORE INTO main.sentiment (symbol, ts, pct) SELECT symbol, ts, pct FROM seed.sentiment WHERE symbol=?", (sym,)).rowcount
            with conn:
                added["instruments"] = conn.execute("INSERT OR IGNORE INTO main.instruments (symbol, name, market, category, payout, open, first_seen, last_seen) "
                                                    "SELECT symbol, name, market, category, payout, open, first_seen, last_seen FROM seed.instruments").rowcount
        finally:
            conn.execute("DETACH DATABASE seed")
            conn.close()
        return added
    finally:
        if tar is not None:
            tar.close()
        shutil.rmtree(tmp, ignore_errors=True)


def _main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parent.parent
    default_db = Path(os.getenv("QXS_DB", root / "data" / DB_NAME))
    ap = argparse.ArgumentParser(prog="python -m app.seed", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    mk = sub.add_parser("make", help="pack a database and its job files into a seed archive")
    mk.add_argument("--db", default=str(default_db))
    mk.add_argument("--out", default=str(root.parent / "seed" / "tradzen-seed.tar.gz"))
    mg = sub.add_parser("merge", help="add the candles / sentiment / instruments of a seed (or .db) to this database")
    mg.add_argument("seed")
    mg.add_argument("--db", default=str(default_db))
    args = ap.parse_args(argv)
    try:
        if args.cmd == "make":
            info = make_seed(args.db, args.out)
            c = info["counts"]
            print(f"seed written: {args.out}  ({info['bytes'] / 1e6:.1f} MB)")
            print("  " + ", ".join(f"{k} {v:,}" for k, v in c.items() if k != "schema_version") + f", {len(info['files'])} job files, schema v{c['schema_version']}")
        else:
            res = merge_market(args.seed, args.db, lambda i, n, s, tf: print(f"\r  pair {i}/{n}", end="", flush=True))
            print("\nadded: " + ", ".join(f"{k} {v:,}" for k, v in res.items()))
    except SeedError as e:
        print(f"error: {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
