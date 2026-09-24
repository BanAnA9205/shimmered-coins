"""Download a Terraria dedicated-server build from terraria.org and extract its data.

The "PC dedicated server" zips are free downloads:
``https://terraria.org/api/download/pc-dedicated-server/terraria-server-<code>.zip`` where
``<code>`` is the version with the dots removed (1.4.5.8 -> 1458). Not every patch got a zip;
``KNOWN_SERVERS`` lists the ones verified to exist. 1.4.0.x has no zips at all (Steam betas only).

``fetch()`` downloads the zip into ``<data>/downloads/``, unzips it, runs the extractor on the
server binary, builds ``<data>/<version>/db.sqlite`` and then deletes the zip and the extracted
server files. Everything lands under the gitignored data directory, so no Re-Logic assets are
committed.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path

DOWNLOAD_URL = "https://terraria.org/api/download/pc-dedicated-server/terraria-server-{}.zip"
USER_AGENT = "shimmered-coins/0.1 (+https://github.com/BanAnA9205/shimmered-coins)"
_TIMEOUT = 60

# zip codes HEAD-verified on terraria.org (2026-09-24). The version label is what the extractor
# is expected to report; the label also names the output directory.
KNOWN_SERVERS = {
    "1.4.1.2": "1412",
    "1.4.2.3": "1423",
    "1.4.3.6": "1436",
    "1.4.4.1": "1441",
    "1.4.4.2": "1442",
    "1.4.4.3": "1443",
    "1.4.4.4": "1444",
    "1.4.4.6": "1446",
    "1.4.4.9": "1449",
    "1.4.5.0": "1450",
    "1.4.5.1": "1451",
    "1.4.5.2": "1452",
    "1.4.5.3": "1453",
    "1.4.5.4": "1454",
    "1.4.5.6": "1456",
    "1.4.5.7": "1457",
    "1.4.5.8": "1458",
}

_CodeToVersion = {code: v for v, code in KNOWN_SERVERS.items()}


class FetchError(RuntimeError):
    pass


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def resolve_version(user: str) -> tuple[str, str]:
    """A version like '1.4.5.8' (or a bare code like '1458') -> (version label, zip code)."""
    user = user.strip().lower()
    if user in KNOWN_SERVERS:
        return user, KNOWN_SERVERS[user]
    if user in _CodeToVersion:
        return _CodeToVersion[user], user
    digits = user.replace(".", "")
    if digits.isdigit() and 3 <= len(digits) <= 4:
        return user, digits  # a patch not in the table: derive the code and hope it exists
    raise FetchError(f"cannot read a version from {user!r}; expected e.g. '1.4.5.8'")


def server_url(code: str) -> str:
    return DOWNLOAD_URL.format(code)


def head(url: str) -> int | None:
    """Content length of the download, or None when it does not exist."""
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            return int(r.headers.get("Content-Length", 0))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise FetchError(f"terraria.org answered {e.code} for {url}") from e
    except urllib.error.URLError as e:
        raise FetchError(f"cannot reach terraria.org: {e.reason}") from e


def download(url: str, dest: Path, progress: Callable[[int, int], None] | None = None) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r, open(dest, "wb") as f:
            total = int(r.headers.get("Content-Length", 0))
            done = 0
            while True:
                chunk = r.read(1 << 18)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    except BaseException:
        dest.unlink(missing_ok=True)  # no half-written zips left behind
        raise


def extract_zip(zip_path: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():  # zip-slip guard
            if name.startswith(("/", "\\")) or ".." in Path(name).parts:
                raise FetchError(f"unsafe entry in {zip_path.name}: {name!r}")
        z.extractall(dest)


def find_server_exe(extracted: Path) -> Path:
    pref = "Windows" if platform.system() == "Windows" else "Linux"
    exes = sorted(extracted.rglob("TerrariaServer.exe"))
    for e in exes:
        if f"{pref}/" in e.as_posix():
            return e
    if exes:
        return exes[0]
    raise FetchError(f"no TerrariaServer.exe in {extracted}")


def ensure_extractor(root: Path) -> Path:
    """Build the C# extractor if needed; returns its .exe path."""
    exe = root / "extractor" / "bin" / "TerrariaExtractor.exe"
    sources = [root / "extractor" / "TerrariaExtractor.cs", root / "extractor" / "Probing.cs"]
    if not all(s.exists() for s in sources):
        raise FetchError(f"no extractor sources under {root / 'extractor'}; run from a checkout")
    fresh = exe.exists() and all(exe.stat().st_mtime >= s.stat().st_mtime for s in sources)
    if fresh:
        return exe
    script = root / "extractor" / ("build.cmd" if platform.system() == "Windows" else "build.sh")
    if platform.system() != "Windows":
        missing = [t for t in ("mcs", "mono") if not shutil.which(t)]
        if missing:
            raise FetchError(
                "building the extractor needs Mono ("
                + "sudo apt-get install mono-mcs mono-runtime"
                + ")"
            )
    cmd = ["cmd", "/c", str(script)] if platform.system() == "Windows" else ["sh", str(script)]
    if subprocess.run(cmd).returncode != 0:
        raise FetchError("extractor build failed")
    if not exe.exists():
        raise FetchError(f"built but {exe} is missing")
    return exe


def run_extractor(server_exe: Path, dump_json: Path) -> None:
    root = repo_root()
    exe = ensure_extractor(root)
    prefix = [] if platform.system() == "Windows" else ["mono"]

    def run(binary: Path) -> int:
        cmd = [*prefix, str(binary), str(server_exe), "--out", str(dump_json)]
        return subprocess.run(cmd).returncode

    rc = run(exe)
    if rc != 0:
        exe32 = root / "extractor" / "bin" / "TerrariaExtractor32.exe"
        if exe32.exists():
            print("retrying with the 32-bit extractor")
            rc = run(exe32)
    if rc != 0:
        raise FetchError("extractor failed; try its --probe flag for a report to file an issue")


def fetch(
    version: str,
    data_root: Path,
    *,
    confirm: Callable[[str], bool] | None = None,
    extract_fn: Callable[[Path, Path], None] = run_extractor,
    keep: bool = False,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Full pipeline for one version: download, unzip, extract, build the db, clean up.

    Returns the db path. `confirm` (when given) gets one question before anything is
    downloaded; `extract_fn` exists so tests can stub out the Mono-based extractor.
    """
    from .curate import build_db

    label, code = resolve_version(version)
    url = server_url(code)
    size = head(url)
    if size is None:
        raise FetchError(
            f"terraria.org has no server zip for {label} ({url}); available: "
            + ", ".join(KNOWN_SERVERS)
            + ". 1.4.0.x exists only as Steam beta branches."
        )
    if confirm is not None and not confirm(
        f"download {url} ({size / 1e6:.1f} MB), extract the game data, build the dataset, "
        f"then delete the server files? [Y/n] "
    ):
        raise FetchError("aborted")

    downloads = data_root / "downloads"
    zip_path = downloads / f"terraria-server-{code}.zip"
    print(f"downloading {url} ({size / 1e6:.1f} MB) -> {zip_path}")
    download(url, zip_path, progress)
    extracted = downloads / f"terraria-server-{code}"
    print(f"unzipping -> {extracted}")
    extract_zip(zip_path, extracted)

    version_dir = data_root / label
    dump_json = version_dir / "dump.json"
    dump_json.parent.mkdir(parents=True, exist_ok=True)
    print(f"extracting game data -> {dump_json} (this reads the assembly; it can take a while)")
    extract_fn(find_server_exe(extracted), dump_json)
    db, _ = build_db(dump_json, out=version_dir / "db.sqlite")
    print(f"built {db}")

    if keep:
        print(f"kept {zip_path} and {extracted}")
    else:
        shutil.rmtree(extracted, ignore_errors=True)
        zip_path.unlink(missing_ok=True)
        downloads.rmdir()  # fails silently when non-empty, which is fine
    return db


def choose_version(ask: Callable[[str], str] | None = None) -> str:
    """Interactive menu over the known versions; returns the user's choice."""
    ask = ask or input
    print("known Terraria server versions on terraria.org (verified 2026-09-24):")
    items = sorted(KNOWN_SERVERS.items(), key=lambda kv: int(kv[1]))
    for k, (ver, code) in enumerate(items, 1):
        print(f"  {k:>2}. {ver}  (zip {code})")
    while True:
        pick = ask("version or number (q to quit): ").strip()
        if pick.lower() in ("q", "quit", ""):
            raise FetchError("aborted")
        if pick.isdigit() and 1 <= int(pick) <= len(items):
            return items[int(pick) - 1][0]
        try:
            resolve_version(pick)
            return pick
        except FetchError:
            print(f"  not a version I know; try like '1.4.5.8' or a number 1-{len(items)}")


def data_root() -> Path:
    return Path(os.environ.get("SHIMCOINS_DATA", "data"))


def list_servers() -> int:
    """Live-probe every known zip; prints what is downloadable. Exit code for the CLI."""
    for ver, code in sorted(KNOWN_SERVERS.items(), key=lambda kv: int(kv[1])):
        size = head(server_url(code))
        if size is None:
            print(f"{ver} ({code}): gone from terraria.org")
        else:
            print(f"{ver} ({code}): {size / 1e6:.1f} MB")
    return 0


def _default_progress(done: int, total: int) -> None:
    if total:
        sys.stdout.write(f"\r{done / 1e6:6.1f} / {total / 1e6:.1f} MB")
        if done == total:
            sys.stdout.write("\n")


__all__ = [
    "FetchError",
    "KNOWN_SERVERS",
    "choose_version",
    "data_root",
    "download",
    "extract_zip",
    "fetch",
    "find_server_exe",
    "head",
    "list_servers",
    "repo_root",
    "resolve_version",
    "run_extractor",
    "server_url",
]
