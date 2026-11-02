"""Clan tag database (data/clans.json) used by `®identify` and `®scan`."""
import json
import os
import re
import time
import unicodedata
from typing import Optional

from . import config, logger

TTL = 60.0
_cache: Optional[dict] = None
_cache_at = 0.0
_ZERO_WIDTH = re.compile("[​-‍﻿︎️]")


def normalize(s) -> str:
    return _ZERO_WIDTH.sub("", unicodedata.normalize("NFKC", str(s or ""))).strip()


def _file():
    return config.get().paths.data / "clans.json"


def load_clans() -> dict:
    global _cache, _cache_at
    if _cache is not None and time.time() - _cache_at < TTL:
        return _cache
    try:
        with open(_file(), encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("clans"), list):
            data = {"clans": []}
    except (OSError, ValueError) as e:
        logger.warn(f"clans.json not loaded: {e}")
        data = {"clans": []}
    _cache, _cache_at = data, time.time()
    return data


def save_clans(data: dict) -> None:
    global _cache, _cache_at
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, f)
    _cache, _cache_at = data, time.time()


def match_name(display_name) -> list:
    if not display_name or not isinstance(display_name, str):
        return []
    name = normalize(display_name)
    out = []
    for clan in load_clans()["clans"]:
        tag = normalize((clan or {}).get("tag"))
        if tag and tag in name:
            out.append({**clan, "method": "exact"})
    return out


def confidence(matches: list) -> int:
    return 98 if any(m.get("method") == "exact" for m in matches) else 0


def add_clan(clan: dict) -> None:
    if not clan.get("name") or not clan.get("tag"):
        raise ValueError("Clan name and tag are required.")
    data = load_clans()
    data["clans"] = [c for c in data["clans"] if normalize(c.get("tag")) != normalize(clan["tag"])]
    try:
        members = int(float(clan.get("members") or 0))
    except (TypeError, ValueError):
        members = 0
    data["clans"].append(
        {
            "name": str(clan["name"]).strip(),
            "tag": str(clan["tag"]).strip(),
            "description": str(clan.get("description") or "").strip(),
            "members": members,
            "age": clan.get("age") or "unknown",
            "threat": clan.get("threat") or "UNKNOWN",
        }
    )
    save_clans(data)


def remove_clan(tag: str) -> None:
    data = load_clans()
    data["clans"] = [c for c in data["clans"] if normalize(c.get("tag")) != normalize(tag)]
    save_clans(data)


def list_clans() -> list:
    return load_clans()["clans"]
