"""Where persistent state lives (db.json, clans.json, WhatsApp sessions).

Resolution order for each directory:
  1. DATA_DIR / SESSION_DIR            – explicit override
  2. PERSIST_DIR or RAILWAY_VOLUME_MOUNT_PATH  – a mounted volume: <mount>/data and <mount>/sessions
  3. ./data and ./sessions             – local development only (WIPED on every container redeploy)

Railway lets a service have ONE volume, so data and sessions share it (<mount>/data, <mount>/sessions).
"""
import os
import shutil
import tempfile
from pathlib import Path

SEED_DIR = Path(__file__).resolve().parent / "seed"  # bundled defaults, copied into the data dir once


def _pick(candidates):
    for c in candidates:
        try:
            Path(c).mkdir(parents=True, exist_ok=True)
            if os.access(c, os.W_OK):
                return Path(c)
        except OSError:
            continue
    return None


def volume_root():
    """Mount path of a persistent volume if the host provides one (and it is writable)."""
    for name in ("PERSIST_DIR", "RAILWAY_VOLUME_MOUNT_PATH"):
        v = os.environ.get(name, "").strip()
        if v:
            return _pick([Path(v).resolve()])
    return None


def _resolve(env_name: str, folder: str) -> Path:
    env = os.environ.get(env_name, "").strip()
    if env:
        return Path(env).resolve()
    root = volume_root()
    if root:
        return _pick([root / folder]) or (Path(tempfile.gettempdir()) / f"xcore-{folder}")
    tmp = Path(tempfile.gettempdir()) / f"xcore-{folder}"
    return _pick([Path.cwd() / folder, Path.home() / ".shadowborne" / folder, tmp]) or tmp


def data_dir() -> Path:
    return _resolve("DATA_DIR", "data")


def session_dir() -> Path:
    return _resolve("SESSION_DIR", "sessions")


def ensure_dir(d) -> Path:
    p = Path(d)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _on_mount(path: Path) -> bool:
    """True if any parent directory of `path` (excluding '/') is a mount point."""
    p = path.resolve()
    while p != p.parent:
        if os.path.ismount(p):
            return True
        p = p.parent
    return False


def status(data=None, sessions=None) -> dict:
    """Describe where state is stored and whether it will survive a redeploy."""
    data = Path(data) if data else data_dir()
    sessions = Path(sessions) if sessions else session_dir()
    root = volume_root()
    on_railway = any(k in os.environ for k in ("RAILWAY_ENVIRONMENT", "RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID"))
    in_container = Path("/.dockerenv").exists() or on_railway
    warnings = []
    if on_railway:
        if not root:
            warnings.append(
                "No Railway volume detected — db.json and WhatsApp sessions are WIPED on every deploy. "
                "Add a volume to this service (mount path e.g. /data)."
            )
        elif not (_under(data, root) and _under(sessions, root)):
            warnings.append(f"DATA_DIR/SESSION_DIR are outside the volume ({root}) — that data is wiped on every deploy.")
    elif in_container and not root and not (_on_mount(data) and _on_mount(sessions)):
        warnings.append("Running in a container with no mounted volume — data will not survive a redeploy.")
    return {
        "data": data,
        "sessions": sessions,
        "volume": root,
        "persistent": not warnings,
        "warnings": warnings,
    }


def bootstrap(data=None, sessions=None) -> dict:
    """Create the directories and copy bundled defaults (clans.json) in if missing. Never overwrites."""
    st = status(data, sessions)
    ensure_dir(st["data"])
    ensure_dir(st["sessions"])
    seeded = []
    if SEED_DIR.is_dir():
        for src in SEED_DIR.iterdir():
            dst = st["data"] / src.name
            if src.is_file() and not dst.exists():
                shutil.copy2(src, dst)
                seeded.append(src.name)
    st["seeded"] = seeded
    return st
