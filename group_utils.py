"""Defensive helpers for Neonize group metadata across supported versions."""
import re
from typing import Any

from .context import norm_jid


def value(obj: Any, *names: str, default=None):
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj[name]
        found = getattr(obj, name, None)
        if found is not None:
            return found
    return default


def jid_text(jid: Any) -> str:
    if jid is None:
        return ""
    user = value(jid, "User", "user", default=None)
    server = value(jid, "Server", "server", default=None)
    if user is not None:
        return f"{user}@{server or 's.whatsapp.net'}"
    return str(jid)


def number_from_jid(jid: Any) -> str:
    return re.sub(r"\D", "", jid_text(jid).split("@")[0].split(":")[0])


def participants(info: Any) -> list:
    result = value(info, "Participants", "participants", "Members", "members", default=[])
    try:
        return list(result or [])
    except TypeError:
        return []


def participant_jid(participant: Any) -> str:
    return norm_jid(jid_text(value(participant, "JID", "Jid", "jid", "ID", "id", default="")))


def is_admin(participant: Any) -> bool:
    role = str(value(participant, "Admin", "admin", "Role", "role", default="") or "").lower()
    return bool(value(participant, "IsAdmin", "is_admin", "Admin", "admin", default=False)) or role in {"admin", "superadmin", "super_admin", "creator"}


def is_superadmin(participant: Any) -> bool:
    role = str(value(participant, "Admin", "admin", "Role", "role", default="") or "").lower()
    return bool(value(participant, "IsSuperAdmin", "is_super_admin", "SuperAdmin", "superadmin", default=False)) or role in {"superadmin", "super_admin", "creator"}


def find_participant(info: Any, number: str):
    clean = re.sub(r"\D", "", str(number))
    return next((p for p in participants(info) if number_from_jid(participant_jid(p)) == clean), None)


def group_subject(info: Any) -> str:
    return str(value(info, "GroupName", "Name", "name", "Subject", "subject", default="Group") or "Group")
