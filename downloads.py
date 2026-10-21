"""Plugin downloads: the newest .jar in DOWNLOADS_DIR is the one customers get.

Files are not public: the website streams them only to signed-in users with an
active license. A new version is published by uploading it from the admin panel
(admin + fresh 2FA) or by copying a .jar into the folder (scp); nothing else to do.
"""

import hashlib
import json
import os
import re
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

KEEP = 5                      # versions kept on disk (newest is the one served)
MAX_UPLOAD = 100 * 1024 * 1024
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_cache = {}


def folder():
    path = Path(os.getenv("DOWNLOADS_DIR", "") or Path(__file__).resolve().parent / "data" / "downloads")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _plugin_meta(path):
    """Name and version from plugin.yml / paper-plugin.yml inside the jar (no YAML library needed)."""
    meta = {"name": None, "version": None}
    try:
        with zipfile.ZipFile(path) as jar:
            for entry in ("paper-plugin.yml", "plugin.yml"):
                if entry in jar.namelist():
                    text = jar.read(entry).decode("utf-8", "replace")
                    for key in ("name", "version"):
                        m = re.search(rf"^{key}:\s*['\"]?([^'\"\r\n#]+)", text, re.M)
                        if m and not meta[key]:
                            meta[key] = m.group(1).strip()
    except (zipfile.BadZipFile, OSError):
        pass
    return meta


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _info(path):
    st = path.stat()
    key = (str(path), st.st_mtime_ns, st.st_size)
    if key not in _cache:
        meta = _plugin_meta(path)
        notes_file = path.with_suffix(".json")
        notes = ""
        if notes_file.exists():
            try:
                notes = json.loads(notes_file.read_text(encoding="utf-8")).get("changelog", "")
            except (ValueError, OSError):
                notes = ""
        version = meta["version"] or "unknown"
        name = meta["name"] or "Mythras"
        _cache[key] = {
            "path": path,
            "file": path.name,
            "name": name,
            "version": version,
            "download_name": f"{_SAFE.sub('-', name)}-{_SAFE.sub('-', version)}.jar",
            "size": st.st_size,
            "size_mb": round(st.st_size / 1048576, 2),
            "sha256": _sha256(path),
            "uploaded": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(timespec="seconds"),
            "changelog": notes,
        }
    return _cache[key]


def versions():
    """All stored jars, newest first. Files still being copied (*.part) are ignored."""
    jars = [p for p in folder().glob("*.jar") if p.is_file()]
    jars.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [_info(p) for p in jars]


def latest():
    items = versions()
    return items[0] if items else None


def save_upload(storage, changelog=""):
    """Store an uploaded jar. Returns its info. Raises ValueError with a readable message."""
    original = (storage.filename or "").lower()
    if not original.endswith(".jar"):
        raise ValueError("Upload a .jar file.")
    target_dir = folder()
    tmp = target_dir / f".upload-{int(time.time() * 1000)}.part"
    storage.save(tmp)
    try:
        if tmp.stat().st_size == 0:
            raise ValueError("The file is empty.")
        if not zipfile.is_zipfile(tmp):
            raise ValueError("This is not a valid .jar file.")
        meta = _plugin_meta(tmp)
        if not meta["version"]:
            raise ValueError("No plugin.yml with a version was found inside the jar.")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        final = target_dir / f"{_SAFE.sub('-', meta['name'] or 'mythras')}-{_SAFE.sub('-', meta['version'])}-{stamp}.jar"
        if changelog.strip():
            final.with_suffix(".json").write_text(json.dumps({"changelog": changelog.strip()[:2000]}), encoding="utf-8")
        os.replace(tmp, final)          # atomic: customers never get a half-written file
    finally:
        if tmp.exists():
            tmp.unlink()
    prune()
    return _info(final)


def prune():
    for old in versions()[KEEP:]:
        try:
            old["path"].unlink(missing_ok=True)
            old["path"].with_suffix(".json").unlink(missing_ok=True)
        except OSError:
            # file busy (e.g. being downloaded right now): it is removed on the next upload
            pass
