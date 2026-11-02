"""Ban-check API client (provider is configurable through env vars)."""
import re
from typing import Optional

import httpx

from . import config


def normalize_phone(p) -> str:
    p = re.sub(r"\D", "", str(p or ""))
    if not p:
        raise ValueError("A phone number is required.")
    return p


_TRUE = {"banned", "blocked", "true", "yes"}
_FALSE = {"safe", "not banned", "not_banned", "false", "no", "active"}


def _interpret(raw) -> Optional[bool]:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        s = raw.lower()
        if s in _TRUE:
            return True
        if s in _FALSE:
            return False
    return None


async def check_ban(phone) -> dict:
    cfg = config.get().api
    phone = normalize_phone(phone)
    headers = {cfg.header: f"{cfg.prefix} {cfg.key}" if cfg.prefix else cfg.key}
    payload = {cfg.phone_field: phone}
    async with httpx.AsyncClient(timeout=cfg.timeout) as http:
        if cfg.method == "GET":
            r = await http.request("GET", cfg.url, headers=headers, params=payload)
        else:
            r = await http.request(cfg.method, cfg.url, headers=headers, json=payload)
    if not 200 <= r.status_code < 300:
        raise RuntimeError(f"Ban API returned HTTP {r.status_code}")
    try:
        d = r.json()
    except ValueError:
        d = {}
    if not isinstance(d, dict):
        d = {}
    raw = next((d[k] for k in ("banned", "isBanned", "blocked", "status") if d.get(k) is not None), None)
    return {
        "banned": _interpret(raw),
        "reason": d.get("reason") or d.get("message") or d.get("detail") or "",
        "raw": d,
    }
