"""Build the Chrome extension for your server:  python deploy/package_extension.py https://signals.example.com

Writes dist/tradzen-extension-<domain>.zip, ready to send to users (Chrome: chrome://extensions -> Developer mode -> Load unpacked, after unzipping)
or to upload as an unlisted Chrome Web Store item. The build:
  * talks to your server by default and is allowed to reach only that server (no other host permission),
  * keeps the side panel (it signs itself in from the extension's token) and has no developer / test files.
Users sign in once in the extension popup with their Tradzen username and password (an access token from "My account" still works too).

The server offers the same file itself (a download button on the landing page and in the admin panel: /download/extension.zip); this script is for making it by hand.
"""
from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from app import extpack  # noqa: E402

SRC = ROOT / "extension"


def build(url: str, out_dir: Path) -> Path:
    try:
        origin = extpack.origin_of(url)
        data = extpack.build_zip(SRC, origin)
        left = extpack.local_address_left(extpack.build_files(SRC, origin))
    except extpack.PackError as e:
        raise SystemExit(str(e))
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"tradzen-extension-{urlparse(origin).hostname}.zip"
    out.write_bytes(data)
    print(f"built {out}  ({out.stat().st_size // 1024} KB)  for {origin}")
    if left:
        print("  note: local-address text remains in:", ", ".join(left))
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    build(sys.argv[1], ROOT / "dist")
