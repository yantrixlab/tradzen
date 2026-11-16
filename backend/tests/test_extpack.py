"""The Chrome extension packaged by the server itself: the build, the public download, the buttons on the landing page, and the places it must stay closed."""
import io
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import extpack  # noqa: E402
from test_auth import ADMIN, ADMIN_PW, _cleanup, _client, _main  # noqa: E402

SRC = Path(__file__).resolve().parent.parent.parent / "extension"
ORIGIN = "https://signals.example.com"


def test_the_build_points_at_one_server_and_only_that_one():
    files = extpack.build_files(SRC, ORIGIN)
    mf = json.loads(files["manifest.json"])
    assert mf["host_permissions"] == [ORIGIN + "/*"] and "optional_host_permissions" not in mf and "side_panel" not in mf and "sidePanel" not in mf["permissions"]
    assert mf["name"] == "Tradzen" and mf["version"] == extpack.version(SRC)
    assert f"const DEFAULT_BACKEND = '{ORIGIN}';" in files["background.js"].decode() and f"const DEFAULT_URL = '{ORIGIN}';" in files["popup.js"].decode()
    assert "sidepanel.html" not in files and "sidepanel.js" not in files
    assert not any(n.startswith("test_") or n.startswith("dev/") for n in files), "no test or developer files"
    assert "hook.js" in files and "icons/icon128.png" in files
    z1, z2 = extpack.build_zip(SRC, ORIGIN), extpack.build_zip(SRC, ORIGIN)
    assert z1 == z2, "the same input gives the same file"
    with zipfile.ZipFile(io.BytesIO(z1)) as z:
        assert z.testzip() is None and "manifest.json" in z.namelist()
    for bad in ("http://signals.example.com", "https://signals.example.com/admin", "https://", "https://a b.com", "javascript:alert(1)", "https://x.com?y=1"):
        try:
            extpack.origin_of(bad)
        except extpack.PackError:
            continue
        raise AssertionError(f"{bad!r} must be refused")
    try:
        extpack.build_zip(Path(tempfile.mkdtemp()), ORIGIN)
    except extpack.PackError:
        pass
    else:
        raise AssertionError("no extension files, no zip")


def test_the_command_line_script_still_builds_it():
    sys.path.insert(0, str(SRC.parent / "deploy"))
    import package_extension
    out = package_extension.build(ORIGIN, Path(tempfile.mkdtemp()))
    assert out.name == "tradzen-extension-signals.example.com.zip" and out.read_bytes() == extpack.build_zip(SRC, ORIGIN)


def test_download_and_buttons():
    main = _main(QXS_ENV="production", QXS_SECRET_KEY="k" * 32, QXS_ADMIN_USER=ADMIN, QXS_ADMIN_PASSWORD=ADMIN_PW)
    anon = _client(main)
    info = anon.get("/download/extension.json").json()
    assert info["available"] is True and info["version"] == extpack.version(SRC) and info["server"] == "https://testserver", info
    r = anon.get("/download/extension.zip")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip" and "tradzen-extension-testserver.zip" in r.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert json.loads(z.read("manifest.json"))["host_permissions"] == ["https://testserver/*"], "built for the address it was asked at"
    page = anon.get("/").text
    assert page.count("/download/extension.zip") == 3 and "{{" not in page and "Download the Chrome extension" in page and "Load unpacked" in page
    assert anon.get("/landing").text.count("/download/extension.zip") == 3
    # the configured public address wins over whatever Host header arrives
    os.environ["QXS_PUBLIC_URL"] = ORIGIN
    try:
        z = zipfile.ZipFile(io.BytesIO(anon.get("/download/extension.zip").content))
        assert json.loads(z.read("manifest.json"))["host_permissions"] == [ORIGIN + "/*"]
        assert anon.get("/download/extension.json").json()["server"] == ORIGIN
    finally:
        del os.environ["QXS_PUBLIC_URL"]
    # a server that is not on https (a local one) offers nothing, and the page has no dead button
    from fastapi.testclient import TestClient
    plain = TestClient(main.app, base_url="http://testserver")
    assert plain.get("/download/extension.json").json()["available"] is False and plain.get("/download/extension.zip").status_code == 404
    assert "/download/extension.zip" not in plain.get("/landing").text and "{{" not in plain.get("/landing").text
    # no extension files on the server: the same
    keep = main.EXT_DIR
    main.EXT_DIR = Path(tempfile.mkdtemp())
    try:
        assert anon.get("/download/extension.json").json()["available"] is False and anon.get("/download/extension.zip").status_code == 404
        assert "/download/extension.zip" not in anon.get("/landing").text
    finally:
        main.EXT_DIR = keep
    # the download holds nothing private, but it is the only thing outside /api that is not a page: nothing else under /download exists
    assert anon.get("/download/../etc/passwd").status_code in (404, 400) and anon.get("/download/").status_code == 404


if __name__ == "__main__":
    try:
        for name, fn in list(globals().items()):
            if name.startswith("test_"):
                fn()
                print("ok", name)
    finally:
        _cleanup()
