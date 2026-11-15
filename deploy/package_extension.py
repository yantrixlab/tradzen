"""Build the Chrome extension for your server:  python deploy/package_extension.py https://signals.example.com

Writes dist/tradzen-extension-<domain>.zip, ready to send to users (Chrome: chrome://extensions -> Developer mode -> Load unpacked, after unzipping)
or to upload as an unlisted Chrome Web Store item. The build:
  * talks to your server by default and is allowed to reach only that server (no other host permission),
  * has no side panel (the hosted panel is opened in a browser tab instead) and no developer / test files.
Users sign in once in the extension popup with their Tradzen username and password (an access token from "My account" still works too).
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import zipfile
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "extension"
SKIP = {"dev", "README.md"}


def sub(text: str, old: str, new: str, what: str) -> str:
    if old not in text:
        raise SystemExit(f"cannot find {what} in the extension source - did it change? ({old[:60]!r})")
    return text.replace(old, new)


def build(url: str, out_dir: Path) -> Path:
    u = urlparse(url)
    if u.scheme != "https" or not u.hostname or u.path not in ("", "/") or u.query or u.fragment:
        raise SystemExit("give the plain https address of your server, e.g. https://signals.example.com")
    origin = f"https://{u.netloc}"
    work = out_dir / "build"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    for f in SRC.rglob("*"):
        rel = f.relative_to(SRC)
        if f.is_dir() or rel.parts[0] in SKIP or f.name.startswith("test_") or f.suffix == ".map":
            continue
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, work / rel)

    bg = work / "background.js"
    bg.write_text(sub(bg.read_text(encoding="utf-8"), "const DEFAULT_BACKEND = 'http://127.0.0.1:8787';", f"const DEFAULT_BACKEND = '{origin}';", "DEFAULT_BACKEND"), encoding="utf-8")
    pj = work / "popup.js"
    pj.write_text(sub(pj.read_text(encoding="utf-8"), "const DEFAULT_URL = 'http://127.0.0.1:8787';", f"const DEFAULT_URL = '{origin}';", "DEFAULT_URL"), encoding="utf-8")
    ph = work / "popup.html"
    t = ph.read_text(encoding="utf-8")
    t = sub(t, 'placeholder="http://127.0.0.1:8787"', f'placeholder="{origin}"', "server placeholder")
    t = sub(t, 'id="panel">Open signal panel</button>', 'id="panel" hidden>Open signal panel</button>', "side panel button")
    t = sub(t, "Send market data to the local backend", "Send my trades to the server", "forward label")
    ph.write_text(t, encoding="utf-8")

    mf = json.loads((work / "manifest.json").read_text(encoding="utf-8"))
    mf["host_permissions"] = [f"{origin}/*"]
    mf.pop("optional_host_permissions", None)
    mf.pop("side_panel", None)
    mf["permissions"] = [p for p in mf["permissions"] if p != "sidePanel"]
    mf["name"] = "Tradzen"
    mf["description"] = "Read-only helper for your own logged-in Quotex tab: shows your signals as on-page alerts with sound and keeps your trade history. Never trades, clicks or logs in."
    (work / "manifest.json").write_text(json.dumps(mf, indent=2) + "\n", encoding="utf-8")

    (work / "sidepanel.html").unlink(missing_ok=True)
    (work / "sidepanel.js").unlink(missing_ok=True)
    left = [str(f.relative_to(work)) for f in work.rglob("*") if f.is_file() and f.suffix in (".js", ".html", ".json") and re.search(r"127\.0\.0\.1|localhost", f.read_text(encoding="utf-8"))]
    # the popup keeps the words "127.0.0.1" / "localhost" in its own local-server check; that is intended
    out = out_dir / f"tradzen-extension-{u.hostname}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(work.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(work).as_posix())
    print(f"built {out}  ({out.stat().st_size // 1024} KB)  for {origin}")
    if left:
        print("  note: local-address text remains in:", ", ".join(left))
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    build(sys.argv[1], ROOT / "dist")
