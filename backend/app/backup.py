"""Backups of the databases (the main one and every user's private one) with SQLite's online backup, so a copy is consistent even while the server is writing.

A backup is a folder  data/backups/YYYYMMDD-HHMMSS/  holding one file per database. The newest `keep` folders are kept. A nightly one runs by itself on a hosted server
(QXS_BACKUP=on|off, QXS_BACKUP_HOUR in UTC, QXS_BACKUP_KEEP). To restore: stop the server, copy the files back over data/signals.db and data/users/*.db, start it.
"""
from __future__ import annotations

import shutil
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

_lock = threading.Lock()
FOLDER_FMT = "%Y%m%d-%H%M%S"


def backups_dir(db_path: str | Path) -> Path:
    return Path(db_path).parent / "backups"


def _copy(src_path: Path, dst_path: Path) -> int:
    src = sqlite3.connect(str(src_path), timeout=60)
    dst = sqlite3.connect(str(dst_path))
    try:
        src.backup(dst)                                   # consistent snapshot, readers and writers are not blocked for long
    finally:
        dst.close()
        src.close()
    return dst_path.stat().st_size


def list_backups(db_path: str | Path) -> list[dict]:
    root = backups_dir(db_path)
    out = []
    if root.is_dir():
        for d in sorted((p for p in root.iterdir() if p.is_dir()), reverse=True):
            files = [f for f in d.rglob("*.db")]
            out.append({"name": d.name, "files": len(files), "bytes": sum(f.stat().st_size for f in files)})
    return out


def run_backup(db_path: str | Path, keep: int = 7) -> dict:
    """Back up the main database and every user database; remove the oldest backups beyond `keep`."""
    db_path = Path(db_path)
    if str(db_path) == ":memory:" or not db_path.exists():
        raise ValueError("there is no database file to back up")
    with _lock:
        base = datetime.now(timezone.utc).strftime(FOLDER_FMT)
        stamp, n = base, 1
        while (backups_dir(db_path) / stamp).exists():                 # two backups in the same second (a double click, nightly + manual) must not collide
            n += 1
            stamp = f"{base}-{n}"
        out = backups_dir(db_path) / stamp
        out.mkdir(parents=True, exist_ok=False)
        total, files = 0, 0
        try:
            total += _copy(db_path, out / db_path.name)
            files += 1
            users = db_path.parent / "users"
            if users.is_dir():
                (out / "users").mkdir()
                for f in sorted(users.glob("*.db")):
                    total += _copy(f, out / "users" / f.name)
                    files += 1
        except Exception:
            shutil.rmtree(out, ignore_errors=True)          # never leave a half backup that looks complete
            raise
        olds = sorted((p for p in backups_dir(db_path).iterdir() if p.is_dir()), reverse=True)[max(1, int(keep)):]
        for old in olds:
            shutil.rmtree(old, ignore_errors=True)
        return {"name": stamp, "files": files, "bytes": total, "removed": len(olds)}


def maybe_nightly(db_path: str | Path, hour: int = 4, keep: int = 7, now: float | None = None) -> dict | None:
    """Called every minute: make today's backup once, after `hour` o'clock UTC, if there is none yet today."""
    t = datetime.fromtimestamp(now if now is not None else time.time(), tz=timezone.utc)
    if t.hour < hour:
        return None
    today = t.strftime("%Y%m%d")
    if any(b["name"].startswith(today) for b in list_backups(db_path)):
        return None
    return run_backup(db_path, keep)
