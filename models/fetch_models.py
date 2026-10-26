#!/usr/bin/env python3
"""Download, verify and extract the third-party scene models listed in manifest.json.

Standard library only. Usage (from anywhere):

    python models/fetch_models.py            # fetch + verify + extract every model
    python models/fetch_models.py --only bistro
    python models/fetch_models.py --check    # verify what is on disk, never download

Layout under the cache directory (default: models/cache/):

    <id>.lock                                per-model exclusive lock
  "format": "zip" models (default):
    downloads/<archive>.zip                  verified archives
    <id>/<zip stem>/...                      extracted archive
    <id>/<zip stem>/.complete.json           completion marker (archive sha256 + member sizes)
  "format": "files" models (individual downloads, no archive):
    <id>/<relative path>                     each file at its manifest path
    <id>/.complete.json                      completion marker (sha256 of every file)

Every step is transactional: files are downloaded to a temporary file and renamed into
place only after size and sha256 match; archives are extracted into a staging directory,
validated, then renamed into place; the completion marker is written last. A marker is
trusted only if its sha256 values equal the manifest's and the files still match it.
The committed manifest is the only authority and is never modified by this script.

Exit status: 0 if every selected model is verified, 1 on any mismatch or failure.
"""

import argparse
import fcntl
import hashlib
import json
import os
import posixpath
import re
import shutil
import sys
import tempfile
import time
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
CHUNK = 1 << 20
MARKER = ".complete.json"
STAGING_PREFIX = ".staging-"
USER_AGENT = "pvhv-fetch-models/1.0 (python urllib)"
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class FetchError(Exception):
    """A verification or download failure that must end the run with exit status 1."""


def log(msg):
    print(msg, flush=True)


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024.0


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(CHUNK)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def zip_stem(entry):
    return os.path.splitext(posixpath.basename(entry["path"]))[0]


def _inside(path, root):
    """Return realpath(path) if it lies strictly inside realpath(root); raise FetchError otherwise.

    Called before every delete, rename destination and extraction target, so that neither a
    manifest entry nor a symlink in the cache can make the script modify anything outside it.
    """
    real = os.path.realpath(path)
    real_root = os.path.realpath(root)
    if not real.startswith(real_root + os.sep):
        raise FetchError(f"refusing to modify {path!r}: resolves to {real!r}, outside {real_root!r}")
    return real


def _check_rel_path(where, rel):
    """Reject absolute paths, backslashes, and empty, '.', '..' or hidden components."""
    if not isinstance(rel, str) or not rel or rel.startswith("/") or "\\" in rel:
        raise FetchError(f"{where}: unsafe path {rel!r}")
    for part in rel.split("/"):
        if part in ("", ".", "..") or part.startswith("."):
            raise FetchError(f"{where}: unsafe path {rel!r}")


def load_manifest(path):
    """Load and validate the manifest. Raises FetchError before anything on disk is touched."""
    with open(path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    models = manifest["models"] if isinstance(manifest, dict) else manifest
    ids, targets, paths = set(), set(), set()
    for m in models:
        for key in ("id", "files", "extract_to"):
            if key not in m:
                raise FetchError(f"manifest {path}: model entry missing '{key}'")
        for key in ("id", "extract_to"):
            if not isinstance(m[key], str) or not SAFE_NAME.match(m[key]):
                raise FetchError(f"manifest {path}: model {key} {m[key]!r} is not a single safe "
                                 f"path component ({SAFE_NAME.pattern})")
        if m["id"] in ids:
            raise FetchError(f"manifest {path}: duplicate model id {m['id']!r}")
        if m["extract_to"] in targets:
            raise FetchError(f"manifest {path}: duplicate extract_to {m['extract_to']!r}")
        ids.add(m["id"])
        targets.add(m["extract_to"])
        fmt = m.get("format", "zip")
        if fmt not in ("zip", "files"):
            raise FetchError(f"manifest {path}: model '{m['id']}' has unknown format {fmt!r}")
        if not isinstance(m["files"], list) or not m["files"]:
            raise FetchError(f"manifest {path}: model '{m['id']}' has no files")
        for e in m["files"]:
            for key in ("url", "path", "sha256", "bytes"):
                if key not in e:
                    raise FetchError(f"manifest {path}: model '{m['id']}' file entry missing '{key}'")
            where = f"manifest {path}: model '{m['id']}'"
            rel = e["path"]
            _check_rel_path(where, rel)
            parts = rel.split("/")
            if fmt == "zip":
                if len(parts) != 2 or parts[0] != "downloads" or not parts[1].endswith(".zip") \
                        or not SAFE_NAME.match(parts[1][:-len(".zip")]):
                    raise FetchError(f"{where}: archive path {rel!r} is not 'downloads/<safe name>.zip'")
            elif len(parts) < 2 or parts[0] != m["extract_to"]:
                raise FetchError(f"{where}: file path {rel!r} is not under '{m['extract_to']}/'")
            if rel in paths:
                raise FetchError(f"{where}: duplicate file path {rel!r}")
            paths.add(rel)
    return models


# ---------------------------------------------------------------------------- archives

def verify_archive(path, entry):
    """Return None if the archive at `path` matches the manifest entry, else a reason string."""
    size = os.path.getsize(path)
    if size != entry["bytes"]:
        return f"size {size} B != manifest {entry['bytes']} B"
    digest = sha256_file(path)
    if digest != entry["sha256"]:
        return f"sha256 {digest} != manifest {entry['sha256']}"
    return None


def download(entry, dest, root):
    """Stream entry['url'] into a unique temp file next to `dest`; rename into place only if verified."""
    _inside(dest, root)
    directory = os.path.dirname(dest)
    os.makedirs(directory, exist_ok=True)
    _inside(dest, root)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix="." + os.path.basename(dest) + ".", suffix=".part")
    h = hashlib.sha256()
    got = 0
    try:
        req = urllib.request.Request(entry["url"], headers={"User-Agent": USER_AGENT})
        with os.fdopen(fd, "wb") as out, urllib.request.urlopen(req, timeout=60) as resp:
            total = entry["bytes"]
            last = time.monotonic()
            while True:
                block = resp.read(CHUNK)
                if not block:
                    break
                out.write(block)
                h.update(block)
                got += len(block)
                now = time.monotonic()
                if now - last >= 10:
                    log(f"    ... {human(got)} / {human(total)} ({100.0 * got / total:.0f}%)")
                    last = now
            out.flush()
            os.fsync(out.fileno())
        os.chmod(tmp, 0o644)
        if got != entry["bytes"]:
            raise FetchError(f"{entry['url']}: downloaded {got} B, manifest says {entry['bytes']} B")
        digest = h.hexdigest()
        if digest != entry["sha256"]:
            raise FetchError(f"{entry['url']}: downloaded sha256 {digest} != manifest {entry['sha256']}")
        _inside(tmp, root)
        _inside(dest, root)
        os.replace(tmp, dest)
    except BaseException:
        if os.path.exists(tmp):
            _inside(tmp, root)
            os.unlink(tmp)
        raise


def remove_stale_parts(dest, root):
    """Remove leftover temp downloads of this archive (safe: the caller holds the model lock)."""
    directory = os.path.dirname(dest)
    if not os.path.isdir(directory):
        return
    _inside(dest, root)
    prefix = "." + os.path.basename(dest) + "."
    for name in os.listdir(directory):
        if name.startswith(prefix) and name.endswith(".part"):
            log(f"    removing stale partial download {name}")
            stale = os.path.join(directory, name)
            _inside(stale, root)
            os.unlink(stale)


# ---------------------------------------------------------------------------- extraction

def safe_members(zf, root):
    """Return {member name: uncompressed size} for files; reject any path escaping `root`."""
    members = {}
    root_abs = os.path.realpath(root)
    for info in zf.infolist():
        name = info.filename
        norm = posixpath.normpath(name)
        if name.startswith("/") or "\\" in name or norm == ".." or norm.startswith("../") \
                or os.path.isabs(name) or ":" in name.split("/")[0]:
            raise FetchError(f"zip member path escapes the target: {name!r}")
        target = os.path.realpath(os.path.join(root_abs, norm))
        if target != root_abs and not target.startswith(root_abs + os.sep):
            raise FetchError(f"zip member path escapes the target: {name!r}")
        if not info.is_dir():
            members[norm] = info.file_size
    return members


def tree_problems(root, members, limit=5):
    """List members missing from `root` or with the wrong size (at most `limit` reasons)."""
    problems = []
    for name, size in members.items():
        p = os.path.join(root, name)
        if not os.path.isfile(p):
            problems.append(f"missing {name}")
        elif os.path.getsize(p) != size:
            problems.append(f"size of {name} is {os.path.getsize(p)} B, expected {size} B")
        if len(problems) >= limit:
            break
    return problems


def read_marker(target):
    try:
        with open(os.path.join(target, MARKER), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def marker_status(target, entry):
    """Return (ok, reason). ok only if the marker sha equals the manifest sha and the tree matches."""
    if not os.path.isdir(target):
        return False, "not extracted"
    marker = read_marker(target)
    if marker is None:
        return False, "no valid completion marker (interrupted extraction?)"
    if marker.get("sha256") != entry["sha256"]:
        return False, f"marker sha256 {marker.get('sha256')} != manifest {entry['sha256']}"
    members = marker.get("members")
    if not isinstance(members, dict) or not members:
        return False, "marker has no member list"
    problems = tree_problems(target, members)
    if problems:
        return False, "extracted tree differs from marker: " + "; ".join(problems)
    return True, f"{len(members)} files"


def extract(archive, entry, model_dir, target):
    """Extract into a unique staging dir, validate, rename to `target`, then write the marker."""
    _inside(target, model_dir)
    os.makedirs(model_dir, exist_ok=True)
    staging = tempfile.mkdtemp(dir=model_dir, prefix=STAGING_PREFIX)
    _inside(staging, model_dir)
    os.chmod(staging, 0o755)
    try:
        with zipfile.ZipFile(archive) as zf:
            members = safe_members(zf, staging)
            zf.extractall(staging)
        problems = tree_problems(staging, members)
        if problems:
            raise FetchError(f"{archive}: extraction incomplete: " + "; ".join(problems))
        _inside(target, model_dir)
        os.rename(staging, target)
    except BaseException:
        _inside(staging, model_dir)
        shutil.rmtree(staging, ignore_errors=True)
        raise
    marker = {
        "sha256": entry["sha256"],
        "bytes": entry["bytes"],
        "url": entry["url"],
        "archive": entry["path"],
        "members": members,
    }
    write_marker(target, marker, model_dir)
    return len(members)


def write_marker(directory, marker, root):
    """Write `directory`/.complete.json atomically (temp file + rename); `directory` must be in `root`."""
    dest = os.path.join(directory, MARKER)
    _inside(dest, root)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=MARKER + ".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(marker, f, indent=1, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o644)
    _inside(tmp, root)
    os.replace(tmp, dest)


def remove_stale_staging(model_dir):
    if not os.path.isdir(model_dir):
        return
    for name in os.listdir(model_dir):
        if name.startswith(STAGING_PREFIX):
            log(f"    removing stale staging dir {name}")
            stale = os.path.join(model_dir, name)
            _inside(stale, model_dir)
            shutil.rmtree(stale)


# ---------------------------------------------------------------------------- per model

class ModelLock:
    """Exclusive (fetch) or shared (check) fcntl lock on <cache>/<id>.lock."""

    def __init__(self, cache, model_id, exclusive):
        self.path = os.path.join(cache, model_id + ".lock")
        self.mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        self.fd = None

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        _inside(self.path, os.path.dirname(self.path))
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(self.fd, self.mode | fcntl.LOCK_NB)
        except BlockingIOError:
            log(f"    waiting for lock {self.path} ...")
            fcntl.flock(self.fd, self.mode)
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)


def model_root(model, cache):
    """Return cache/<extract_to>, refusing it if it (or a symlink on the way) resolves outside the cache."""
    model_dir = os.path.join(cache, model["extract_to"])
    _inside(model_dir, cache)
    return model_dir


def fetch_zip_model(model, cache):
    model_dir = model_root(model, cache)
    with ModelLock(cache, model["id"], exclusive=True):
        remove_stale_staging(model_dir)
        for entry in model["files"]:
            archive = os.path.join(cache, entry["path"])
            target = os.path.join(model_dir, zip_stem(entry))
            log(f"  [{model['id']}] {posixpath.basename(entry['path'])}")
            _inside(archive, cache)
            _inside(target, model_dir)
            remove_stale_parts(archive, cache)
            ok, reason = marker_status(target, entry)
            if ok:
                log(f"    already verified, skipped ({reason})")
                continue
            if os.path.lexists(target):
                log(f"    discarding extracted tree: {reason}")
                _inside(target, model_dir)
                shutil.rmtree(target)
            if os.path.exists(archive):
                log(f"    verifying cached archive {entry['path']} ...")
                bad = verify_archive(archive, entry)
                if bad:
                    raise FetchError(f"cached archive {archive}: {bad}. "
                                     "Delete it to download again, or fix the manifest.")
            else:
                log(f"    downloading {entry['url']} ({human(entry['bytes'])})")
                download(entry, archive, cache)
            log(f"    archive verified (sha256 {entry['sha256'][:16]}...)")
            n = extract(archive, entry, model_dir, target)
            log(f"    extracted {n} files to {os.path.relpath(target, cache)}/")


def check_zip_model(model, cache):
    """Verify archives and extracted trees without downloading. Returns a list of failures."""
    failures = []
    if not os.path.isdir(cache):
        return [f"[{model['id']}] cache directory {cache} does not exist"]
    model_dir = model_root(model, cache)
    with ModelLock(cache, model["id"], exclusive=False):
        for entry in model["files"]:
            archive = os.path.join(cache, entry["path"])
            target = os.path.join(model_dir, zip_stem(entry))
            name = posixpath.basename(entry["path"])
            if os.path.exists(archive):
                bad = verify_archive(archive, entry)
                if bad:
                    failures.append(f"[{model['id']}] archive {entry['path']}: {bad}")
                    log(f"  [{model['id']}] {name}: archive MISMATCH ({bad})")
                else:
                    log(f"  [{model['id']}] {name}: archive OK")
            else:
                log(f"  [{model['id']}] {name}: archive not cached")
            ok, reason = marker_status(target, entry)
            if ok:
                log(f"  [{model['id']}] {name}: extracted tree OK ({reason})")
            else:
                failures.append(f"[{model['id']}] {os.path.relpath(target, cache)}: {reason}")
                log(f"  [{model['id']}] {name}: extracted tree NOT OK ({reason})")
    return failures


def files_marker_status(model, cache, hash_files=False):
    """Return (ok, reason) for a "files" model: marker sha256 per path equals the manifest's,
    and every file is present with its manifest size (and, with `hash_files`, its sha256)."""
    model_dir = os.path.join(cache, model["extract_to"])
    marker = read_marker(model_dir)
    if marker is None:
        return False, "no valid completion marker (interrupted fetch?)"
    expected = {e["path"]: e["sha256"] for e in model["files"]}
    if marker.get("files") != expected:
        return False, "marker sha256 list differs from the manifest"
    for e in model["files"]:
        p = os.path.join(cache, e["path"])
        if not os.path.isfile(p):
            return False, f"missing {e['path']}"
        if os.path.getsize(p) != e["bytes"]:
            return False, f"size of {e['path']} is {os.path.getsize(p)} B, expected {e['bytes']} B"
        if hash_files:
            digest = sha256_file(p)
            if digest != e["sha256"]:
                return False, f"sha256 of {e['path']} is {digest}, manifest {e['sha256']}"
    return True, f"{len(expected)} files"


def fetch_files_model(model, cache):
    model_dir = model_root(model, cache)
    for entry in model["files"]:
        _inside(os.path.join(cache, entry["path"]), model_dir)
    with ModelLock(cache, model["id"], exclusive=True):
        ok, reason = files_marker_status(model, cache, hash_files=True)
        if ok:
            log(f"  [{model['id']}] already verified, skipped ({reason})")
            return
        log(f"  [{model['id']}] {reason}")
        marker = os.path.join(model_dir, MARKER)
        if os.path.exists(marker):
            _inside(marker, model_dir)
            os.unlink(marker)
        for entry in model["files"]:
            dest = os.path.join(cache, entry["path"])
            _inside(dest, model_dir)
            remove_stale_parts(dest, model_dir)
            if os.path.exists(dest):
                bad = verify_archive(dest, entry)
                if not bad:
                    log(f"    {entry['path']}: verified, kept")
                    continue
                log(f"    {entry['path']}: {bad}; discarding")
                _inside(dest, model_dir)
                os.unlink(dest)
            log(f"    downloading {entry['url']} ({human(entry['bytes'])})")
            download(entry, dest, model_dir)
            log(f"    {entry['path']}: verified (sha256 {entry['sha256'][:16]}...)")
        write_marker(model_dir, {
            "files": {e["path"]: e["sha256"] for e in model["files"]},
            "bytes": {e["path"]: e["bytes"] for e in model["files"]},
        }, model_dir)
        log(f"  [{model['id']}] {len(model['files'])} files complete in {model['extract_to']}/")


def check_files_model(model, cache):
    """Hash every file of a "files" model and validate its marker. Returns a list of failures."""
    failures = []
    if not os.path.isdir(cache):
        return [f"[{model['id']}] cache directory {cache} does not exist"]
    model_root(model, cache)
    with ModelLock(cache, model["id"], exclusive=False):
        for entry in model["files"]:
            p = os.path.join(cache, entry["path"])
            bad = verify_archive(p, entry) if os.path.isfile(p) else "missing"
            if bad:
                failures.append(f"[{model['id']}] {entry['path']}: {bad}")
                log(f"  [{model['id']}] {entry['path']}: MISMATCH ({bad})")
            else:
                log(f"  [{model['id']}] {entry['path']}: OK")
        ok, reason = files_marker_status(model, cache)
        if ok:
            log(f"  [{model['id']}] completion marker OK ({reason})")
        else:
            failures.append(f"[{model['id']}] {model['extract_to']}/{MARKER}: {reason}")
            log(f"  [{model['id']}] completion marker NOT OK ({reason})")
    return failures


def fetch_model(model, cache):
    if model.get("format", "zip") == "files":
        fetch_files_model(model, cache)
    else:
        fetch_zip_model(model, cache)


def check_model(model, cache):
    if model.get("format", "zip") == "files":
        return check_files_model(model, cache)
    return check_zip_model(model, cache)


def summary(models, cache):
    log("")
    log("Summary:")
    for m in models:
        log(f"  {m['id']}: {m.get('name', '')}")
        for key in ("main_obj", "main_mtl"):
            if key in m:
                p = os.path.join(cache, m[key])
                state = "present" if os.path.isfile(p) else "MISSING"
                log(f"    {key}: {p} ({state})")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--manifest", default=os.path.join(HERE, "manifest.json"),
                    help="manifest to use (default: manifest.json next to this script)")
    ap.add_argument("--cache", default=os.path.join(HERE, "cache"),
                    help="cache directory (default: cache/ next to this script)")
    ap.add_argument("--only", metavar="ID", action="append",
                    help="process only this model id (repeatable)")
    ap.add_argument("--check", action="store_true",
                    help="verify archives and extracted trees on disk; never download or extract")
    args = ap.parse_args(argv)

    try:
        models = load_manifest(args.manifest)
    except (OSError, ValueError, FetchError, KeyError, TypeError) as e:
        print(f"ERROR: cannot read manifest {args.manifest}: {e}", file=sys.stderr)
        return 1
    if args.only:
        known = {m["id"] for m in models}
        unknown = [i for i in args.only if i not in known]
        if unknown:
            print(f"ERROR: unknown model id(s) {unknown}; manifest has {sorted(known)}", file=sys.stderr)
            return 1
        models = [m for m in models if m["id"] in args.only]
    cache = os.path.abspath(args.cache)

    if args.check:
        log(f"Checking {len(models)} model(s) in {cache} against {args.manifest}")
        failures = []
        for m in models:
            try:
                failures += check_model(m, cache)
            except FetchError as e:
                failures.append(f"[{m['id']}] {e}")
            except OSError as e:
                failures.append(f"[{m['id']}] {type(e).__name__}: {e}")
        summary(models, cache)
        if failures:
            print("\nCHECK FAILED:", file=sys.stderr)
            for f in failures:
                print(f"  {f}", file=sys.stderr)
            return 1
        log("\nAll selected models verified.")
        return 0

    log(f"Fetching {len(models)} model(s) into {cache}")
    for m in models:
        try:
            fetch_model(m, cache)
        except FetchError as e:
            print(f"\nERROR: {e}", file=sys.stderr)
            return 1
        except (OSError, zipfile.BadZipFile) as e:
            print(f"\nERROR: [{m['id']}] {type(e).__name__}: {e}", file=sys.stderr)
            return 1
    summary(models, cache)
    log("\nAll selected models verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
