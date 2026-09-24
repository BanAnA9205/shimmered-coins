"""`shimcoins data fetch`: download / unzip / locate / build / cleanup, stubbed end to end.

No network and no Mono: the zip comes from a localhost HTTP server and the "extractor" is a
Python stub that writes a minimal dump. The only untested-by-design parts are the real terraria.org
download (verified manually) and the Mono invocation (`run_extractor`).
"""

import http.server
import json
import threading
import zipfile
from pathlib import Path

import pytest
from shimmered_coins.data import fetch
from shimmered_coins.data.fetch import FetchError


def test_resolve_version():
    assert fetch.resolve_version("1.4.5.8") == ("1.4.5.8", "1458")
    assert fetch.resolve_version(" 1.4.4.9 ") == ("1.4.4.9", "1449")
    assert fetch.resolve_version("1458") == ("1.4.5.8", "1458")
    # undocumented patch: the code is derived, the label kept as typed
    assert fetch.resolve_version("1.4.4.5") == ("1.4.4.5", "1445")
    with pytest.raises(FetchError):
        fetch.resolve_version("v1.4.5")
    with pytest.raises(FetchError):
        fetch.resolve_version("1.4.5.8.1")


def test_server_url():
    assert fetch.server_url("1449") == (
        "https://terraria.org/api/download/pc-dedicated-server/terraria-server-1449.zip"
    )


def make_server_zip(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("1458/readme.txt", "junk")
        z.writestr("1458/Windows/TerrariaServer.exe", "fake windows server")
        z.writestr("1458/Mac/TerrariaServer.exe", "fake mac server")
        z.writestr("1458/Linux/TerrariaServer.exe", "fake linux server")


def test_extract_zip_and_find_server_exe(tmp_path):
    zip_path = tmp_path / "server.zip"
    make_server_zip(zip_path)
    out = tmp_path / "out"
    fetch.extract_zip(zip_path, out)
    exe = fetch.find_server_exe(out)
    assert exe.read_text() == (
        "fake windows server" if fetch.platform.system() == "Windows" else "fake linux server"
    )
    assert fetch.find_server_exe(out / "1458") == exe


def test_extract_zip_rejects_zip_slip(tmp_path):
    zip_path = tmp_path / "evil.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("../../../etc/passwd", "nope")
    with pytest.raises(FetchError):
        fetch.extract_zip(zip_path, tmp_path / "out")


@pytest.fixture
def zip_server(tmp_path):
    make_server_zip(tmp_path / "terraria-server-1458.zip")

    def handler(*a):
        return http.server.SimpleHTTPRequestHandler(*a, directory=str(tmp_path))

    handler.log_message = lambda *a: None
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/terraria-server-{{}}.zip"
    httpd.shutdown()


def stub_extractor(server_exe: Path, dump_json: Path) -> None:
    assert server_exe.name == "TerrariaServer.exe"
    dump_json.write_text(
        json.dumps(
            {
                "schema": 1,
                "version": "1.4.5.8",
                "items": [{"id": 1, "name": "A", "value": 100}],
                "npcs": [{"id": 1, "name": "Merchant"}],
                "shops": [{"npc": 1, "item": 1}],
            }
        )
    )


def test_fetch_pipeline(zip_server, tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, "DOWNLOAD_URL", zip_server)
    data_root = tmp_path / "data"
    seen = []

    def progress(done, total):
        seen.append((done, total))

    db = fetch.fetch(
        "1.4.5.8",
        data_root,
        confirm=lambda q: seen.append(q) or True,
        extract_fn=stub_extractor,
        progress=progress,
    )
    assert db == data_root / "1.4.5.8" / "db.sqlite"
    assert db.is_file() and (data_root / "1.4.5.8" / "dump.json").is_file()
    assert seen[-1][0] == seen[-1][1]  # download reported completion
    from shimmered_coins.data import load_dataset

    assert load_dataset(db).version == "1.4.5.8"
    assert not (data_root / "downloads").exists()  # cleanup: zip, extracted dir, empty parent


def test_fetch_keep(zip_server, tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, "DOWNLOAD_URL", zip_server)
    data_root = tmp_path / "data"
    fetch.fetch("1458", data_root, confirm=lambda q: True, extract_fn=stub_extractor, keep=True)
    assert (data_root / "downloads" / "terraria-server-1458.zip").is_file()
    assert (data_root / "downloads" / "terraria-server-1458").is_dir()


def test_fetch_confirm_declined(zip_server, tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, "DOWNLOAD_URL", zip_server)
    with pytest.raises(FetchError, match="aborted"):
        fetch.fetch("1.4.5.8", tmp_path, confirm=lambda q: False, extract_fn=stub_extractor)
    assert not (tmp_path / "downloads").exists()


def test_fetch_missing_version(zip_server, tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, "DOWNLOAD_URL", zip_server)
    with pytest.raises(FetchError, match="no server zip"):
        fetch.fetch("1.4.4.5", tmp_path, confirm=lambda q: True, extract_fn=stub_extractor)


def test_choose_version(monkeypatch):
    answers = iter(["nope", "1.4.4.9"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers) or "")
    assert fetch.choose_version() == "1.4.4.9"
    answers = iter(["3"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers) or "")
    assert fetch.choose_version() == "1.4.3.6"  # 3rd oldest known version


def test_cli_fetch_smoke(monkeypatch, tmp_path):
    from shimmered_coins import cli

    calls = {}

    def fake_fetch(version, root, **kw):
        calls["args"] = (version, root, kw)
        return tmp_path / "db.sqlite", None

    monkeypatch.setattr(cli.fetch_mod, "fetch", fake_fetch)
    monkeypatch.setattr(cli.fetch_mod, "data_root", lambda: tmp_path)
    assert cli.main(["data", "fetch", "1.4.5.8", "--yes"]) == 0
    version, root, kw = calls["args"]
    assert (version, root) == ("1.4.5.8", tmp_path)
    assert kw["confirm"] is None and kw["keep"] is False


def test_cli_fetch_needs_version_when_not_a_tty(monkeypatch, capsys):
    import io

    from shimmered_coins.cli import main

    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert main(["data", "fetch"]) == 2
    assert "pass a version" in capsys.readouterr().err
