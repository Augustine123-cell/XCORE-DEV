"""JSON-file database (data/db.json).

The on-disk schema (camelCase keys, millisecond timestamps) is identical to the Node
version, so an existing db.json can be dropped straight into DATA_DIR.
"""
import json
import os
import re
import shutil
import threading
import time
from typing import Any, Optional

from . import config, logger

_lock = threading.RLock()
_cache: Optional[dict] = None

MODES = ("self", "public", "dev", "maintenance", "stealth")
DAY_MS = 86_400_000


def now_ms() -> int:
    return int(time.time() * 1000)


def clean(n: Any) -> str:
    return re.sub(r"\D", "", str(n))


def _read_json(path) -> dict:
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    if not isinstance(d, dict):
        raise ValueError("db root is not a JSON object")
    return d


def _recover(path, err) -> dict:
    """db.json exists but is unreadable: keep the bad file for inspection, fall back to the last good backup."""
    quarantine = path.with_name(f"{path.name}.corrupt-{now_ms()}")
    try:
        os.replace(path, quarantine)
        logger.error(f"{path.name} unreadable ({err}); moved to {quarantine.name}")
    except OSError:
        logger.error(f"{path.name} unreadable ({err})")
    try:
        d = _read_json(path.with_name(path.name + ".bak"))
        logger.warn(f"Restored {path.name} from {path.name}.bak")
        return d
    except (OSError, ValueError):
        return {}


def load() -> dict:
    global _cache
    with _lock:
        if _cache is not None:
            return _cache
        path = config.get().paths.db_file
        try:
            d = _read_json(path)
        except FileNotFoundError:
            d = {}
        except (OSError, ValueError) as e:
            d = _recover(path, e)
        for k in ("sudo", "premium", "banned", "removed", "paired", "buglog", "monitors", "muted"):
            if not isinstance(d.get(k), list):
                d[k] = []
        if not isinstance(d.get("names"), dict):
            d["names"] = {}
        if not isinstance(d.get("telegram_links"), dict):
            d["telegram_links"] = {}
        if not isinstance(d.get("telegram_pending_premium"), dict):
            d["telegram_pending_premium"] = {}
        if not isinstance(d.get("telegram_payments"), list):
            d["telegram_payments"] = []
        if not isinstance(d.get("disabled_sessions"), list):
            d["disabled_sessions"] = []
        ab = d.get("antibug") if isinstance(d.get("antibug"), dict) else {}
        d["antibug"] = {
            "enabled": bool(ab.get("enabled", True)),
            "autoblock": bool(ab.get("autoblock", True)),
            "autopurge": bool(ab.get("autopurge", True)),
            "threshold": int(ab.get("threshold") or 60),
            "trusted": [clean(x) for x in ab.get("trusted", []) if clean(x)] if isinstance(ab.get("trusted"), list) else [],
            "blocked": ab.get("blocked") if isinstance(ab.get("blocked"), dict) else {},
        }
        if d.get("mode") is None:
            d["mode"] = config.get().bot.mode
        if d.get("antiban") is None:
            d["antiban"] = True
        st = d.get("stats") if isinstance(d.get("stats"), dict) else {}
        d["stats"] = {k: int(st.get(k) or 0) for k in ("checks", "banned", "safe")}
        if d.get("startedAt") is None:
            d["startedAt"] = now_ms()
        _cache = d
        return d


def save() -> None:
    with _lock:
        d = load()
        path = config.get().paths.db_file
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(d, indent=2, ensure_ascii=False))
            f.flush()
            os.fsync(f.fileno())  # reach the disk before we swap it in (a redeploy can kill us any moment)
        if path.exists():
            try:
                shutil.copyfile(path, path.with_name(path.name + ".bak"))  # last known-good copy
            except OSError:
                pass
        os.replace(tmp, path)  # atomic: never leaves a half-written db.json


def reset_cache() -> None:
    """Forget the in-memory copy (used by tests)."""
    global _cache
    with _lock:
        _cache = None


# ── sudo ────────────────────────────────────────────────────────────────
def is_sudo(n) -> bool:
    c = clean(n)
    return c in config.get().owner.numbers or c in load()["sudo"]


def add_sudo(n) -> None:
    with _lock:
        c, d = clean(n), load()
        if c not in d["sudo"]:
            d["sudo"].append(c)
            save()


def remove_sudo(n) -> None:
    with _lock:
        c, d = clean(n), load()
        d["sudo"] = [x for x in d["sudo"] if x != c]
        save()


def list_sudo() -> list:
    return list(load()["sudo"])


# ── premium ─────────────────────────────────────────────────────────────
def is_premium(n) -> bool:
    c = clean(n)
    if is_sudo(c):
        return True
    p = next((x for x in load()["premium"] if x["number"] == c), None)
    return bool(p) and (not p.get("until") or p["until"] > now_ms())


def add_premium(n, days: int = 30, plan: str = "standard", by: str = "owner") -> None:
    with _lock:
        c, d = clean(n), load()
        d["premium"] = [x for x in d["premium"] if x["number"] != c]
        d["premium"].append(
            {
                "number": c,
                "until": now_ms() + days * DAY_MS if days > 0 else None,
                "plan": plan,
                "addedAt": now_ms(),
                "addedBy": by,
            }
        )
        save()


def remove_premium(n) -> None:
    with _lock:
        c, d = clean(n), load()
        d["premium"] = [x for x in d["premium"] if x["number"] != c]
        save()


def list_premium() -> list:
    out = []
    for p in load()["premium"]:
        until = p.get("until")
        out.append(
            {
                **p,
                "expired": bool(until and until < now_ms()),
                "daysLeft": max(0, (until - now_ms()) // DAY_MS) if until else "∞",
            }
        )
    return out


def link_telegram_user(user_id: int, number: str) -> None:
    with _lock:
        d = load()
        uid, phone = str(int(user_id)), clean(number)
        d["telegram_links"][uid] = phone
        pending = d["telegram_pending_premium"].get(uid)
        if pending and int(pending.get("until", 0)) > now_ms():
            remaining_days = max(1, (int(pending["until"]) - now_ms() + DAY_MS - 1) // DAY_MS)
            add_premium(phone, remaining_days, "telegram-stars", uid)
            d["telegram_pending_premium"].pop(uid, None)
        save()


def telegram_linked_number(user_id: int) -> str | None:
    return load().get("telegram_links", {}).get(str(int(user_id)))


def grant_telegram_premium(user_id: int, days: int, by: str = "telegram-stars") -> str | None:
    days = int(days)
    if not 1 <= days <= 3650:
        raise ValueError("Premium duration must be between 1 and 3650 days")
    uid = str(int(user_id))
    phone = telegram_linked_number(user_id)
    if phone:
        current = next((p for p in load()["premium"] if p.get("number") == phone), None)
        if current and not current.get("until"):
            return phone  # permanent Premium is not downgraded by a subscription purchase
        if current and current.get("until"):
            remaining = max(0, (int(current["until"]) - now_ms() + DAY_MS - 1) // DAY_MS)
        else:
            remaining = 0
        add_premium(phone, min(3650, days + remaining), "telegram-stars", by)
        return phone
    with _lock:
        d = load()
        existing = d["telegram_pending_premium"].get(uid) or {}
        base = max(now_ms(), int(existing.get("until", 0)))
        d["telegram_pending_premium"][uid] = {"until": base + days * DAY_MS, "days": days, "by": by}
        save()
    return None


def apply_telegram_payment(payment_id: str, user_id: int, days: int) -> tuple[bool, str | None]:
    """Idempotently apply a successful Telegram Stars payment once per charge ID."""
    charge = str(payment_id or "").strip()
    if not charge:
        raise ValueError("Missing Telegram payment charge ID")
    with _lock:
        d = load()
        if any(x.get("charge_id") == charge for x in d["telegram_payments"]):
            return False, telegram_linked_number(user_id)
        d["telegram_payments"].append({"charge_id": charge, "user_id": str(int(user_id)), "days": int(days), "at": now_ms()})
        d["telegram_payments"] = d["telegram_payments"][-1000:]
        save()
        phone = grant_telegram_premium(user_id, days, "telegram-stars")
        return True, phone


def revoke_telegram_premium(user_id: int) -> None:
    uid = str(int(user_id))
    phone = telegram_linked_number(user_id)
    if phone:
        remove_premium(phone)
    with _lock:
        d = load()
        d["telegram_pending_premium"].pop(uid, None)
        save()


# ── local bans ──────────────────────────────────────────────────────────
def is_banned(n) -> bool:
    with _lock:
        c, d = clean(n), load()
        b = next((x for x in d["banned"] if x["number"] == c), None)
        if not b:
            return False
        if b.get("until") and b["until"] < now_ms():  # temp ban expired
            d["banned"] = [x for x in d["banned"] if x["number"] != c]
            save()
            return False
        return True


def ban_user(n, days: int = 0, reason: str = "No reason", by: str = "owner") -> None:
    with _lock:
        c, d = clean(n), load()
        d["banned"] = [x for x in d["banned"] if x["number"] != c]
        d["banned"].append(
            {
                "number": c,
                "until": now_ms() + days * DAY_MS if days > 0 else None,
                "reason": reason,
                "bannedAt": now_ms(),
                "bannedBy": by,
            }
        )
        save()


def unban_user(n) -> None:
    with _lock:
        c, d = clean(n), load()
        d["banned"] = [x for x in d["banned"] if x["number"] != c]
        save()


def list_banned() -> list:
    return [
        {
            **b,
            "daysLeft": max(0, (b["until"] - now_ms()) // DAY_MS) if b.get("until") else "permanent",
        }
        for b in load()["banned"]
    ]


# ── removed users ───────────────────────────────────────────────────────
def is_removed(n) -> bool:
    return clean(n) in load()["removed"]


def remove_user(n) -> None:
    with _lock:
        c, d = clean(n), load()
        d["sudo"] = [x for x in d["sudo"] if x != c]
        d["premium"] = [x for x in d["premium"] if x["number"] != c]
        d["paired"] = [x for x in d["paired"] if x != c]
        if c not in d["removed"]:
            d["removed"].append(c)
        save()


def restore_user(n) -> None:
    with _lock:
        c, d = clean(n), load()
        d["removed"] = [x for x in d["removed"] if x != c]
        save()


def list_removed() -> list:
    return list(load()["removed"])


# ── paired numbers ──────────────────────────────────────────────────────
def is_paired(n) -> bool:
    return clean(n) in load()["paired"]


def add_paired(n) -> None:
    with _lock:
        c, d = clean(n), load()
        if c not in d["paired"]:
            d["paired"].append(c)
            save()


def list_paired() -> list:
    return list(load()["paired"])


def remove_paired(n) -> None:
    with _lock:
        c, d = clean(n), load()
        d["paired"] = [x for x in d["paired"] if clean(x) != c]
        save()


def set_session_disabled(n, disabled: bool = True) -> None:
    with _lock:
        c, d = clean(n), load()
        d["disabled_sessions"] = [x for x in d["disabled_sessions"] if clean(x) != c]
        if disabled:
            d["disabled_sessions"].append(c)
        save()


def is_session_disabled(n) -> bool:
    return clean(n) in {clean(x) for x in load().get("disabled_sessions", [])}


def list_telegram_links() -> list[dict]:
    return [{"telegram_id": int(uid), "number": phone}
            for uid, phone in load().get("telegram_links", {}).items()]


def unlink_telegram_number(number) -> None:
    phone = clean(number)
    with _lock:
        d = load()
        d["telegram_links"] = {uid: linked for uid, linked in d["telegram_links"].items()
                               if clean(linked) != phone}
        save()


def grant_premium_identity(user_id: int, number: str, days: int, by: str = "owner-dashboard") -> None:
    """Link a Telegram identity to a WhatsApp number and grant Premium to that number."""
    uid, phone, duration = str(int(user_id)), clean(number), int(days)
    if not 7 <= len(phone) <= 15:
        raise ValueError("WhatsApp number must contain 7–15 digits with country code")
    if not 1 <= duration <= 3650:
        raise ValueError("Premium duration must be between 1 and 3650 days")
    with _lock:
        d = load()
        d["telegram_links"][uid] = phone
        save()
        current = next((x for x in load()["premium"] if x.get("number") == phone), None)
        remaining = 0
        if current and current.get("until"):
            remaining = max(0, (int(current["until"]) - now_ms() + DAY_MS - 1) // DAY_MS)
        add_premium(phone, min(3650, duration + remaining), "owner-grant", by)


# ── mode / antiban / stats / bug log ────────────────────────────────────
def get_mode() -> str:
    return load()["mode"]


def set_mode(m: str) -> None:
    if m not in MODES:
        raise ValueError(f"Invalid mode: {m}")
    with _lock:
        load()["mode"] = m
        save()


def get_antiban() -> bool:
    return bool(load()["antiban"])


def set_antiban(v: bool) -> None:
    with _lock:
        load()["antiban"] = bool(v)
        save()


def bump_stats(banned: Optional[bool]) -> None:
    with _lock:
        s = load()["stats"]
        s["checks"] += 1
        if banned is True:
            s["banned"] += 1
        elif banned is False:
            s["safe"] += 1
        save()


def get_stats() -> dict:
    return dict(load()["stats"])


def get_started_at() -> int:
    return load()["startedAt"]


def log_bug(entry: dict) -> None:
    with _lock:
        d = load()
        d["buglog"].insert(0, {**entry, "at": now_ms()})
        d["buglog"] = d["buglog"][:200]
        save()


def list_bugs(n: int = 20) -> list:
    return load()["buglog"][:n]


def set_muted(chat: str, number: str, until_ms: int, reason: str = "antibug") -> None:
    with _lock:
        d = load()
        key = (str(chat), clean(number))
        d["muted"] = [x for x in d["muted"] if (x.get("chat"), clean(x.get("number"))) != key]
        d["muted"].append({"chat": key[0], "number": key[1], "until": int(until_ms), "reason": reason})
        save()


def get_mute(chat: str, number: str) -> dict | None:
    with _lock:
        d = load()
        key = (str(chat), clean(number))
        item = next((x for x in d["muted"] if (x.get("chat"), clean(x.get("number"))) == key), None)
        if not item:
            return None
        if int(item.get("until", 0)) <= now_ms():
            d["muted"] = [x for x in d["muted"] if x is not item]
            save()
            return None
        return dict(item)


def clear_mute(chat: str, number: str) -> None:
    with _lock:
        d = load()
        key = (str(chat), clean(number))
        d["muted"] = [x for x in d["muted"] if (x.get("chat"), clean(x.get("number"))) != key]
        save()


# ── antibug settings, trust list and attacker shield ───────────────────
def antibug_settings() -> dict:
    with _lock:
        a = load()["antibug"]
        return {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v) for k, v in a.items()}


def set_antibug_option(key: str, value: Any) -> None:
    if key not in ("enabled", "autoblock", "autopurge", "threshold"):
        raise ValueError(f"Unknown antibug option: {key}")
    with _lock:
        a = load()["antibug"]
        a[key] = int(value) if key == "threshold" else bool(value)
        save()


def antibug_trusted(n) -> bool:
    return clean(n) in load()["antibug"]["trusted"]


def antibug_trust(n, trusted: bool = True) -> None:
    num = clean(n)
    with _lock:
        a = load()["antibug"]
        a["trusted"] = [x for x in a["trusted"] if x != num]
        if trusted and num:
            a["trusted"].append(num)
        save()


def antibug_shield_add(n, strength: int, reason: str, wa_blocked: bool) -> None:
    """Remember an attacker so the bot drops everything they send, even if WhatsApp's own block failed."""
    with _lock:
        load()["antibug"]["blocked"][clean(n)] = {
            "at": now_ms(), "strength": int(strength), "reason": str(reason), "waBlocked": bool(wa_blocked),
        }
        save()


def antibug_shield_remove(n) -> bool:
    with _lock:
        removed = load()["antibug"]["blocked"].pop(clean(n), None)
        save()
        return removed is not None


def antibug_shield_get(n) -> dict | None:
    item = load()["antibug"]["blocked"].get(clean(n))
    return dict(item) if item else None


def antibug_shield_list() -> dict:
    return dict(load()["antibug"]["blocked"])


# ── display names (used by the clan scanner) ────────────────────────────
def save_name(jid: str, name: str) -> None:
    with _lock:
        d = load()
        d["names"][jid] = {"name": str(name), "at": now_ms()}
        if len(d["names"]) > 2000:  # prune the 500 oldest
            for k in sorted(d["names"], key=lambda k: d["names"][k].get("at", 0))[:500]:
                del d["names"][k]
        save()


def get_name(jid: str) -> Optional[str]:
    return (load()["names"].get(jid) or {}).get("name")


# ── monitors ────────────────────────────────────────────────────────────
def add_monitor(url: str, chat_jid: str, added_by: str) -> bool:
    with _lock:
        d = load()
        if any(m["url"] == url for m in d["monitors"]):
            return False
        d["monitors"].append(
            {
                "url": url,
                "chatJid": chat_jid,
                "addedBy": added_by,
                "addedAt": now_ms(),
                "status": "unknown",
                "lastCheck": None,
                "lastDown": None,
                "downSince": None,
                "totalChecks": 0,
                "totalDown": 0,
            }
        )
        save()
        return True


def remove_monitor(url: str) -> None:
    with _lock:
        d = load()
        d["monitors"] = [m for m in d["monitors"] if m["url"] != url]
        save()


def list_monitors() -> list:
    return load()["monitors"]


def update_monitor(url: str, patch: dict) -> None:
    with _lock:
        m = next((x for x in load()["monitors"] if x["url"] == url), None)
        if not m:
            return
        m.update(patch)
        m["lastCheck"] = now_ms()
        save()


# ── roles ───────────────────────────────────────────────────────────────
def get_role(n) -> str:
    c = clean(n)
    if c in config.get().owner.numbers:
        return "OWNER"
    if is_removed(c):
        return "REMOVED"
    if is_banned(c):
        return "BANNED"
    if is_sudo(c):
        return "SUDO"
    if is_premium(c):
        return "PREMIUM"
    if is_paired(c):
        return "PAIRED"
    return "USER"
