"""The Chrome extension packaged for one server: the same code `deploy/package_extension.py` has always built, as a function, so the server itself can hand it to users
(a download button on the landing page and in the admin panel).

The build for https://signals.example.com:
  * talks to that server by default and is allowed to reach only that server (no other host permission),
  * has no side panel (the hosted panel is opened in a browser tab instead) and no developer / test files.
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path
from urllib.parse import urlparse

SKIP = {"dev", "README.md"}
HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?(:\d{1,5})?$")


class PackError(ValueError):
    pass


def origin_of(url: str) -> str:
    """The plain https origin of a server, or PackError."""
    u = urlparse(url)
    if u.scheme != "https" or not u.hostname or u.path not in ("", "/") or u.query or u.fragment or not HOST_RE.match(u.netloc):
        raise PackError("give the plain https address of your server, e.g. https://signals.example.com")
    return f"https://{u.netloc}"


def _sub(text: str, old: str, new: str, what: str) -> str:
    if old not in text:
        raise PackError(f"cannot find {what} in the extension source - did it change? ({old[:60]!r})")
    return text.replace(old, new)


def available(src: Path) -> bool:
    return (src / "manifest.json").is_file() and (src / "background.js").is_file()


def version(src: Path) -> str | None:
    try:
        return json.loads((src / "manifest.json").read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError):
        return None


def build_files(src: Path, origin: str) -> dict[str, bytes]:
    """{path inside the zip: bytes} for the extension that belongs to `origin`."""
    origin = origin_of(origin)
    if not available(src):
        raise PackError("the extension files are not on this server")
    files: dict[str, bytes] = {}
    for f in sorted(src.rglob("*")):
        rel = f.relative_to(src)
        if f.is_dir() or rel.parts[0] in SKIP or f.name.startswith("test_") or f.suffix == ".map":
            continue
        files[rel.as_posix()] = f.read_bytes()

    def text(name: str) -> str:
        return files[name].decode("utf-8")

    def put(name: str, value: str) -> None:
        files[name] = value.encode("utf-8")

    put("background.js", _sub(text("background.js"), "const DEFAULT_BACKEND = 'http://127.0.0.1:8787';", f"const DEFAULT_BACKEND = '{origin}';", "DEFAULT_BACKEND"))
    put("popup.js", _sub(text("popup.js"), "const DEFAULT_URL = 'http://127.0.0.1:8787';", f"const DEFAULT_URL = '{origin}';", "DEFAULT_URL"))
    t = text("popup.html")
    t = _sub(t, 'placeholder="http://127.0.0.1:8787"', f'placeholder="{origin}"', "server placeholder")
    t = _sub(t, 'id="panel">Open signal panel</button>', 'id="panel" hidden>Open signal panel</button>', "side panel button")
    t = _sub(t, "Send market data to the local backend", "Send my trades to the server", "forward label")
    put("popup.html", t)

    mf = json.loads(text("manifest.json"))
    mf["host_permissions"] = [f"{origin}/*"]
    mf.pop("optional_host_permissions", None)
    mf.pop("side_panel", None)
    mf["permissions"] = [p for p in mf["permissions"] if p != "sidePanel"]
    mf["name"] = "Tradzen"
    mf["description"] = "Read-only helper for your own logged-in Quotex tab: shows your signals as on-page alerts with sound and keeps your trade history. Never trades, clicks or logs in."
    put("manifest.json", json.dumps(mf, indent=2) + "\n")

    files.pop("sidepanel.html", None)
    files.pop("sidepanel.js", None)
    return files


def local_address_left(files: dict[str, bytes]) -> list[str]:
    """Files that still mention 127.0.0.1 / localhost (the popup keeps the words for its own local-server check; that is intended)."""
    return [n for n, b in files.items() if n.endswith((".js", ".html", ".json")) and re.search(r"127\.0\.0\.1|localhost", b.decode("utf-8", "replace"))]


def build_zip(src: Path, origin: str) -> bytes:
    files = build_files(src, origin)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(files):
            z.writestr(zipfile.ZipInfo(name, date_time=(2024, 1, 1, 0, 0, 0)), files[name], zipfile.ZIP_DEFLATED)      # a fixed date: the same input gives the same file
    return buf.getvalue()
