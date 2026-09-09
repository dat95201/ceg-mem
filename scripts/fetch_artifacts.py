#!/usr/bin/env python3
"""Fetch the shipped artifacts and install them into this checkout.

    python3 scripts/fetch_artifacts.py --source https://example.org/artifacts.zip [--sha256 HEX]
    python3 scripts/fetch_artifacts.py --source "https://drive.google.com/file/d/<id>/view?usp=sharing"
    python3 scripts/fetch_artifacts.py --source /path/to/artifacts.zip
    python3 scripts/fetch_artifacts.py --source /path/to/artifacts/          # a directory, used in place
    python3 scripts/fetch_artifacts.py --verify [--source DIR]               # MANIFEST.json vs artifacts/ (or DIR)
    python3 scripts/fetch_artifacts.py --verify --installed                  # ... vs the installed copies

Stage 1 of scripts/reproduce.sh (ARTIFACTS=<source>). Four steps, each printed:

  download   http(s) URLs, including Google Drive share links (the "large file,
             cannot scan for viruses" confirmation is followed automatically);
             saved under artifacts/downloads/
  verify     --sha256 (ARTIFACTS_SHA256) against the downloaded or given file
  unpack     .zip or .tar(.gz) into artifacts/ (a single top-level directory
             inside the archive is transparent)
  install    artifacts/cache/**  -> ./cache/         (the model-response cache)
             artifacts/data/<run>/** -> ./data/<run>/
             artifacts/logs/<run>/** -> ./logs/<run>/
             A file that already exists is NEVER overwritten unless --force
             (ARTIFACTS_FORCE=1); the run's own newer state wins. Idempotent:
             a second run installs nothing and says so.

The layout it expects is what scripts/build_artifacts.py writes: MANIFEST.json
(sha256 per file) beside data/ and logs/ (and cache/ if shipped).
"""
from __future__ import annotations

import argparse
import hashlib
import html
import http.cookiejar
import os
import pathlib
import re
import shutil
import sys
import tarfile
import time
import urllib.parse
import urllib.request
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
INSTALL_TOPS = ("cache", "data", "logs")


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} GB"


# ── download ─────────────────────────────────────────────────────────────────
_DRIVE_ID = re.compile(r"(?:/file/d/|[?&]id=)([A-Za-z0-9_-]{20,})")
UA = "Mozilla/5.0 (X11; Linux x86_64) ceg-mem/fetch_artifacts"


def drive_id(url: str) -> str | None:
    if "drive.google.com" not in url and "docs.google.com" not in url:
        return None
    m = _DRIVE_ID.search(url)
    return m.group(1) if m else None


def _opener():
    jar = http.cookiejar.CookieJar()
    # build_opener honours HTTP(S)_PROXY from the environment on its own.
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def _stream(resp, target: pathlib.Path, label: str) -> None:
    total = int(resp.headers.get("Content-Length") or 0)
    done, t0, last = 0, time.time(), 0.0
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as f:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if time.time() - last > 2:
                last = time.time()
                pct = f" {done / total:5.1%}" if total else ""
                print(f"  {label}: {human(done)}{pct}", flush=True)
    print(f"  {label}: {human(done)} in {time.time() - t0:.0f}s")


def _is_html(resp) -> bool:
    return "text/html" in (resp.headers.get("Content-Type") or "")


def _drive_confirm_url(page: str, file_id: str) -> str | None:
    """The second-hop URL for a Drive file too large for the virus scan.

    Two shapes have been in the wild: a link carrying `confirm=<token>` on
    drive.google.com/uc, and a form posting to drive.usercontent.google.com
    with hidden inputs (id, export, confirm, uuid, authuser). Both are read
    off the interstitial page rather than guessed.
    """
    m = re.search(r'<form[^>]+id="download-form"[^>]+action="([^"]+)"', page)
    if m:
        action = html.unescape(m.group(1))
        fields = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', page))
        fields = {k: html.unescape(v) for k, v in fields.items()}
        fields.setdefault("id", file_id)
        fields.setdefault("export", "download")
        fields.setdefault("confirm", "t")
        return action + "?" + urllib.parse.urlencode(fields)
    m = re.search(r"confirm=([0-9A-Za-z_-]+)", page)
    if m:
        return (f"https://drive.google.com/uc?export=download&confirm={m.group(1)}"
                f"&id={file_id}")
    return None


def download(url: str, dest_dir: pathlib.Path) -> pathlib.Path:
    opener = _opener()
    fid = drive_id(url)
    if fid:
        url = f"https://drive.google.com/uc?export=download&id={fid}"
        label = f"drive:{fid[:8]}..."
    else:
        label = url.rsplit("/", 1)[-1] or "download"
    print(f"downloading {url}")
    for hop in range(3):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        resp = opener.open(req, timeout=120)
        if fid and _is_html(resp):
            page = resp.read().decode("utf-8", "replace")
            nxt = _drive_confirm_url(page, fid)
            if nxt is None or hop == 2:
                raise SystemExit(
                    "Google Drive returned a web page instead of the file. Either the file is not "
                    "shared as 'Anyone with the link', the download quota for it is exhausted for "
                    "today, or the link is not a file link. Download it in a browser and pass the "
                    "local path, or place it on a mounted Drive and pass that path.")
            print(f"  confirmation page - following ({hop + 1})")
            url = nxt
            continue
        name = None
        cd = resp.headers.get("Content-Disposition") or ""
        m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd)
        if m:
            name = urllib.parse.unquote(m.group(1)).strip()
        if not name:
            name = urllib.parse.urlparse(url).path.rsplit("/", 1)[-1] or "artifacts.zip"
            if fid:
                name = f"{fid}.zip" if "." not in name else name
        target = dest_dir / name
        _stream(resp, target, label)
        return target
    raise SystemExit("download did not converge")


# ── unpack ───────────────────────────────────────────────────────────────────
def unpack(archive: pathlib.Path, dest: pathlib.Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    print(f"unpacking {archive} -> {dest}")
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                p = pathlib.Path(info.filename)
                if p.is_absolute() or ".." in p.parts:
                    raise SystemExit(f"refusing unsafe archive member {info.filename}")
            zf.extractall(dest)
            n = len(zf.infolist())
    elif tarfile.is_tarfile(archive):
        with tarfile.open(archive) as tf:
            for m in tf.getmembers():
                p = pathlib.Path(m.name)
                if p.is_absolute() or ".." in p.parts:
                    raise SystemExit(f"refusing unsafe archive member {m.name}")
            tf.extractall(dest)
            n = len(tf.getmembers())
    else:
        head = archive.read_bytes()[:200]
        raise SystemExit(f"{archive} is neither a zip nor a tar archive ({human(archive.stat().st_size)}; "
                         f"starts with {head[:60]!r}). A download that returned an HTML error page "
                         f"looks exactly like this.")
    print(f"  {n} entries")


def locate_root(d: pathlib.Path) -> pathlib.Path:
    """The directory that holds MANIFEST.json / data/ / cache/ / logs/.

    An archive built with `zip -r artifacts.zip artifacts/` has one top-level
    directory; build_artifacts.py --zip writes the same shape. Either way the
    installer wants the level that holds data/.
    """
    def looks(p: pathlib.Path) -> bool:
        return (p / "MANIFEST.json").is_file() or any((p / t).is_dir() for t in INSTALL_TOPS)
    if looks(d):
        return d
    kids = [k for k in d.iterdir() if k.is_dir() and k.name != "downloads"]
    if len(kids) == 1 and looks(kids[0]):
        return kids[0]
    hits = [k for k in kids if looks(k)]
    if len(hits) == 1:
        return hits[0]
    raise SystemExit(f"{d} holds no data/, cache/ or logs/ directory and no MANIFEST.json - "
                     f"not an artifacts layout (see scripts/build_artifacts.py)")


# ── install ──────────────────────────────────────────────────────────────────
def install(root: pathlib.Path, into: pathlib.Path, *, force: bool, dry_run: bool) -> dict:
    summary = {}
    for top in INSTALL_TOPS:
        src = root / top
        if not src.is_dir():
            continue
        n_new = n_skip = n_over = 0
        b_new = 0
        for path in sorted(p for p in src.rglob("*") if p.is_file()):
            rel = path.relative_to(root)
            target = into / rel
            if target.exists():
                if force and sha256(target) != sha256(path):
                    if not dry_run:
                        shutil.copy2(path, target)
                    n_over += 1
                else:
                    n_skip += 1
                continue
            if not dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
            n_new += 1
            b_new += path.stat().st_size
        summary[top] = {"installed": n_new, "bytes": b_new, "kept_existing": n_skip,
                        "overwritten": n_over}
        runs = sorted({p.relative_to(src).parts[0] for p in src.rglob("*") if p.is_file()}) \
            if top != "cache" else []
        verb = "would install" if dry_run else "installed"
        print(f"  {top + '/':7s} {verb} {n_new} file(s) ({human(b_new)}), kept {n_skip} existing"
              + (f", overwrote {n_over} (--force)" if n_over else "")
              + (f"   runs: {', '.join(runs)}" if runs else ""))
    if not summary:
        raise SystemExit(f"{root} holds none of {INSTALL_TOPS} - nothing to install")
    return summary


# ── verify ───────────────────────────────────────────────────────────────────
def verify(root: pathlib.Path, *, installed_into: pathlib.Path | None) -> int:
    manifest_path = root / "MANIFEST.json"
    if not manifest_path.is_file():
        print(f"{manifest_path} missing", file=sys.stderr)
        return 2
    import json
    manifest = json.loads(manifest_path.read_text())
    base = installed_into or root
    ok = missing = bad = 0
    for entry in manifest.get("files", []):
        path = base / entry["path"]
        if not path.is_file():
            missing += 1
            print(f"  MISSING  {entry['path']}")
            continue
        if path.stat().st_size != entry["bytes"] or sha256(path) != entry["sha256"]:
            bad += 1
            print(f"  DIFFERS  {entry['path']}")
            continue
        ok += 1
    where = "installed copies" if installed_into else str(root)
    print(f"verify {where}: {ok} ok, {missing} missing, {bad} differ "
          f"(manifest {manifest.get('created')}, commit {str(manifest.get('git', {}).get('commit'))[:12]})")
    return 0 if not (missing or bad) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default=os.environ.get("ARTIFACTS") or None,
                    help="URL, Google Drive share link, archive path or artifacts directory "
                         "(default: $ARTIFACTS)")
    ap.add_argument("--sha256", default=os.environ.get("ARTIFACTS_SHA256") or None,
                    help="expected digest of the archive (default: $ARTIFACTS_SHA256)")
    ap.add_argument("--dest", type=pathlib.Path, default=ROOT / "artifacts",
                    help="where archives are unpacked (default: artifacts/)")
    ap.add_argument("--into", type=pathlib.Path, default=ROOT,
                    help="checkout to install into (default: this repository)")
    ap.add_argument("--force", action="store_true", default=os.environ.get("ARTIFACTS_FORCE") == "1",
                    help="overwrite existing files that differ (default: $ARTIFACTS_FORCE=1)")
    ap.add_argument("--no-install", action="store_true", help="download/verify/unpack only")
    ap.add_argument("--dry-run", action="store_true", help="print what would be installed")
    ap.add_argument("--verify", action="store_true",
                    help="check MANIFEST.json hashes (of --source DIR if given, else artifacts/) and exit")
    ap.add_argument("--installed", action="store_true",
                    help="with --verify: check the installed copies under --into instead")
    args = ap.parse_args(argv)

    dest = args.dest.resolve()
    if args.verify:
        base = dest
        if args.source and not re.match(r"^https?://", args.source):
            base = pathlib.Path(args.source).expanduser().resolve()   # a built tree, before shipping
        root = locate_root(base) if base.is_dir() else base
        return verify(root, installed_into=args.into.resolve() if args.installed else None)
    if not args.source:
        ap.error("--source (or $ARTIFACTS) is required")

    src = args.source
    root: pathlib.Path
    if re.match(r"^https?://", src):
        archive = download(src, dest / "downloads")
    else:
        p = pathlib.Path(src).expanduser().resolve()
        if not p.exists():
            raise SystemExit(f"{p} does not exist")
        archive = None if p.is_dir() else p

    if archive is not None:
        digest = sha256(archive)
        print(f"sha256 {digest}  {archive} ({human(archive.stat().st_size)})")
        if args.sha256:
            if digest.lower() != args.sha256.lower():
                raise SystemExit(f"sha256 mismatch: expected {args.sha256}")
            print("  matches --sha256")
        if not zipfile.is_zipfile(archive) and not tarfile.is_tarfile(archive):
            raise SystemExit(f"{archive} is not an archive ({human(archive.stat().st_size)}). "
                             f"A Drive quota page or a login page saved under the file's name "
                             f"looks like this - download it in a browser and pass the path.")
        unpack(archive, dest)
        root = locate_root(dest)
    else:
        if args.sha256:
            print("  --sha256 given for a directory source: nothing to check (use --verify)")
        root = locate_root(p)
        print(f"using {root} in place")

    if (root / "MANIFEST.json").is_file():
        rc = verify(root, installed_into=None)
        if rc:
            raise SystemExit("artifacts do not match their MANIFEST.json - refusing to install")
    else:
        print("no MANIFEST.json - installing without hash verification")

    if args.no_install:
        return 0
    print(("dry-run: " if args.dry_run else "") + f"installing into {args.into.resolve()}")
    install(root, args.into.resolve(), force=args.force, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
