"""XCORE Anti-Bug engine.

Pipeline for every inbound message (DMs and groups):

  1. Shield   – numbers already countered are dropped instantly.
  2. Analyse  – payload signals (size, control/invisible/combining characters, mass mentions,
                oversized protobuf, unparseable events …) + behaviour signals (flood, duplicates)
                are scored into a 0-100 **bug strength**.
  3. Counter  – at or above the threshold: block the sender on WhatsApp, purge every message
                we saw from them, add them to the bot-level shield, alert the owner.
  4. Report   – the owner receives the strength and an **effectiveness %** computed only from
                actions that really succeeded (nothing is assumed).

Group behaviour from the previous version is kept: when the bot is a group admin it also deletes
the messages and applies the 30-minute soft mute.
"""
import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass, field
import hashlib
import re
import time
import unicodedata
from itertools import groupby

from .. import config, db, logger
from .group_utils import find_participant, is_admin

# ── runtime state (short-lived; persistent state lives in db.json) ──────
_WINDOWS: dict[tuple[str, str], deque] = defaultdict(deque)     # (chat, number) → recent message times
_DUPLICATES: dict[tuple[str, str], deque] = defaultdict(deque)  # (chat, number) → (time, fingerprint)
_TRACK: dict[str, deque] = defaultdict(lambda: deque(maxlen=300))  # number → refs to purge
_ACTIVE: dict[str, float] = {}                                  # numbers being countered right now
_UNPARSEABLE: dict[str, deque] = defaultdict(deque)             # number → times of events we could not parse
_GROUP_CACHE: dict[str, tuple[float, object]] = {}

WINDOW_SECONDS = 10
GROUP_BURST = 7
DM_BURST = 10
TRACK_SECONDS = 180
MUTE_SECONDS = 30 * 60
DEFAULT_THRESHOLD = 60
MAX_SCAN_CHARS = 200_000

RISKY_KINDS = {
    "nativeFlowMessage", "interactiveMessage", "buttonsMessage", "listMessage", "templateMessage",
    "orderMessage", "productMessage", "invoiceMessage", "paymentInviteMessage", "groupInviteMessage",
    "liveLocationMessage", "contactsArrayMessage",
}

_INVISIBLE = set(range(0x200B, 0x2010)) | set(range(0x202A, 0x202F)) | set(range(0x2060, 0x2070)) | {0xFEFF, 0x180E, 0x034F}


# ── data model ──────────────────────────────────────────────────────────
@dataclass
class Signal:
    key: str
    label: str
    points: int
    detail: str = ""


@dataclass
class ThreatReport:
    strength: int
    signals: list[Signal] = field(default_factory=list)
    handled: bool = False          # the pipeline must stop processing this message
    countered: bool = False
    outcome: dict = field(default_factory=dict)

    @property
    def primary(self) -> str:
        return max(self.signals, key=lambda s: s.points).label if self.signals else "clean"

    @property
    def level(self) -> str:
        return level_for(self.strength)


def level_for(strength: int) -> str:
    return "LOW" if strength < 30 else "MEDIUM" if strength < 60 else "HIGH" if strength < 80 else "CRITICAL"


def strength_bar(strength: int) -> str:
    filled = max(0, min(10, round(strength / 10)))
    return "▰" * filled + "▱" * (10 - filled)


def combine(points: list[int]) -> int:
    """Strongest signal dominates; every extra signal adds 35 % of its weight. Capped at 100."""
    if not points:
        return 0
    ordered = sorted(points, reverse=True)
    return min(100, round(ordered[0] + 0.35 * sum(ordered[1:])))


# ── payload analysis ────────────────────────────────────────────────────
def _text_fingerprint(text: str) -> str:
    normalized = re.sub(r"\s+", " ", (text or "").strip().lower())
    return hashlib.sha256(normalized.encode("utf-8", "ignore")).hexdigest()


def analyze_payload(text: str, meta: dict | None = None) -> list[Signal]:
    """Static signals from one message. Pure function: no I/O, safe to unit-test."""
    text, meta = text or "", meta or {}
    out: list[Signal] = []
    size = len(text.encode("utf-8", "ignore"))
    if size > 20000:
        out.append(Signal("oversized", "oversized payload", 90, f"{size} bytes"))
    elif size > 12000:
        out.append(Signal("oversized", "oversized payload", 60, f"{size} bytes"))

    scan = text[:MAX_SCAN_CHARS]
    if scan:
        controls = invisible = combining = 0
        for ch in scan:
            code = ord(ch)
            if (code < 32 and ch not in "\n\r\t") or code == 0x7F:
                controls += 1
            elif code in _INVISIBLE:
                invisible += 1
            elif unicodedata.category(ch) in ("Mn", "Me"):
                combining += 1
        if controls >= 200:
            out.append(Signal("controls", "control-character flood", 85, f"{controls} control chars"))
        elif controls >= 20:
            out.append(Signal("controls", "control-character flood", 55, f"{controls} control chars"))
        if invisible >= 300:
            out.append(Signal("invisible", "invisible-character flood", 85, f"{invisible} invisible/bidi chars"))
        elif invisible >= 30:
            out.append(Signal("invisible", "invisible-character flood", 55, f"{invisible} invisible/bidi chars"))
        if combining >= 400:
            out.append(Signal("combining", "combining-mark overload", 85, f"{combining} combining marks"))
        elif combining >= 80 and combining / max(1, len(scan)) >= 0.5:
            out.append(Signal("combining", "combining-mark overload", 70, f"{combining} combining marks"))

        longest_run = max((sum(1 for _ in g) for _, g in groupby(scan)), default=0)
        if longest_run >= 3000:
            out.append(Signal("charrun", "repeated-character payload", 60, f"{longest_run}× same character"))
        longest_token = max((len(t) for t in scan.split()), default=0)
        if longest_token >= 4000 and longest_run < 3000:
            out.append(Signal("token", "unbroken giant token", 50, f"{longest_token} chars without spaces"))
        urls = len(re.findall(r"https?://", scan, flags=re.I))
        if urls >= 8:
            out.append(Signal("urls", "link flood", 60, f"{urls} links"))
        mentions = len(re.findall(r"@\d{7,15}", scan))
        if mentions >= 12:
            out.append(Signal("mentions-text", "mass-mention payload", 60, f"{mentions} @mentions"))

    mentioned = int(meta.get("mentions") or 0)
    if mentioned >= 1000:
        out.append(Signal("mentions-meta", "mass-mention payload", 85, f"{mentioned} mentioned JIDs"))
    elif mentioned >= 200:
        out.append(Signal("mentions-meta", "mass-mention payload", 60, f"{mentioned} mentioned JIDs"))
    proto = int(meta.get("proto_size") or 0)
    if proto > 262_144:
        out.append(Signal("proto", "oversized message structure", 85, f"{proto} bytes on the wire"))
    elif proto > 65_536:
        out.append(Signal("proto", "oversized message structure", 50, f"{proto} bytes on the wire"))
    if int(meta.get("vcard_len") or 0) > 20_000:
        out.append(Signal("vcard", "oversized contact card", 60, f"{meta['vcard_len']} chars"))
    if meta.get("unparseable"):
        out.append(Signal("unparseable", "unparseable payload", 45, str(meta.get("error") or "")[:80]))
    if meta.get("kind") in RISKY_KINDS:
        out.append(Signal("kind", "interactive/structured message", 20, str(meta["kind"])))
    return out


# ── group helpers (unchanged behaviour) ─────────────────────────────────
async def _group_info(session, chat: str):
    now = time.monotonic()
    cached = _GROUP_CACHE.get(chat)
    if cached and now - cached[0] < 30:
        return cached[1]
    try:
        info = await session.group_info(chat)
        _GROUP_CACHE[chat] = (now, info)
        return info
    except Exception:
        return None


async def _group_admin(session, chat: str, sender_number: str) -> bool:
    info = await _group_info(session, chat)
    if info is None:
        return False
    participant = find_participant(info, sender_number)
    return bool(participant and is_admin(participant))


async def _bot_admin(session, chat: str) -> bool:
    info = await _group_info(session, chat)
    if info is None:
        return False
    participant = find_participant(info, session.number)
    return bool(participant and is_admin(participant))


def _is_group(chat: str) -> bool:
    return str(chat).endswith("@g.us")


def _exempt(session, number: str) -> bool:
    cfg = config.get()
    return bool(number in cfg.owner.numbers or number == session.number or db.is_sudo(number) or db.antibug_trusted(number))


# ── counter actions ─────────────────────────────────────────────────────
async def _try_block(session, msg) -> tuple[str, str]:
    fn = getattr(session, "block_contact", None)
    if not callable(fn):
        return "unsupported", "this session cannot block contacts"
    try:
        await fn(msg.sender)
        return "ok", ""
    except NotImplementedError as exc:
        return "unsupported", str(exc)[:120]
    except Exception as exc:  # noqa: BLE001
        return "failed", str(exc)[:120]


async def _purge(session, refs: list[tuple], can_moderate: bool) -> tuple[int, int, str]:
    """Delete every tracked message. Returns (purged, targets, note)."""
    targets = len(refs)
    purged = 0
    notes: set[str] = set()
    sem = asyncio.Semaphore(8)

    async def one(chat, sender_jid, message_id) -> bool:
        if not message_id:
            notes.add("message id unavailable")
            return False
        async with sem:
            try:
                if _is_group(chat):
                    if not can_moderate:
                        notes.add("bot is not a group admin")
                        return False
                    await session.delete_message(chat, sender_jid, message_id)
                else:
                    fn = getattr(session, "delete_for_me", None)
                    if not callable(fn):
                        notes.add("delete-for-me unsupported")
                        return False
                    await fn(chat, sender_jid, message_id)
                return True
            except NotImplementedError:
                notes.add("delete unsupported by this WhatsApp library")
                return False
            except Exception as exc:  # noqa: BLE001
                logger.warn(f"antibug purge failed: {exc}")
                notes.add("delete rejected by WhatsApp")
                return False

    results = await asyncio.gather(*(one(c, s, m) for c, s, m in refs))
    purged = sum(1 for r in results if r)

    # DM fallback: wipe the whole attacker chat if per-message deletion was not possible.
    dm_chats = {c for c, _, _ in refs if not _is_group(c)}
    if purged < targets and dm_chats:
        fn = getattr(session, "delete_chat", None)
        if callable(fn):
            wiped = 0
            for chat in dm_chats:
                try:
                    await fn(chat)
                    wiped += sum(1 for c, _, _ in refs if c == chat)
                except Exception as exc:  # noqa: BLE001
                    logger.warn(f"antibug chat wipe failed: {exc}")
            if wiped:
                purged = min(targets, max(purged, wiped))
                notes.discard("delete-for-me unsupported")
    return purged, targets, ", ".join(sorted(notes))


def effectiveness(block: str, purged: int, targets: int, shield: bool, settings: dict) -> int:
    """Percent of the counter that is *confirmed*: block 50 · purge 30 · shield 20.
    Components the owner switched off are left out; failed/unsupported ones score zero."""
    weight = earned = 0.0
    if settings.get("autoblock", True):
        weight += 50
        earned += 50 if block == "ok" else 0
    if settings.get("autopurge", True):
        weight += 30
        earned += 30 * (purged / targets) if targets else 30
    weight += 20
    earned += 20 if shield else 0
    return round(100 * earned / weight) if weight else 0


def _block_text(status: str, why: str) -> str:
    return {"ok": "✅ blocked on WhatsApp", "failed": f"❌ block failed ({why})",
            "unsupported": f"➖ not supported here ({why})", "skipped": "⏸ auto-block is OFF"}[status]


def format_report(msg, report: ThreatReport, where: str) -> str:
    o = report.outcome
    detected = "\n".join(f"  • {s.label}" + (f" — {s.detail}" if s.detail else "")
                         for s in sorted(report.signals, key=lambda s: -s.points)[:5]) or "  • behaviour pattern"
    purge_line = f"{o.get('purged', 0)}/{o.get('targets', 0)} messages deleted"
    if o.get("purge_note"):
        purge_line += f" ({o['purge_note']})"
    return (
        "🛡 ANTIBUG — THREAT COUNTERED\n\n"
        f"Attacker: +{msg.sender_number}\n"
        f"Name: {msg.push_name or 'Unavailable'}\n"
        f"Where: {where}\n\n"
        f"Bug strength: {report.strength}/100 {strength_bar(report.strength)} {report.level}\n"
        f"Detected:\n{detected}\n\n"
        "Counter actions:\n"
        f"  • {_block_text(o.get('block', 'skipped'), o.get('block_note', ''))}\n"
        f"  • {purge_line}\n"
        "  • ✅ bot shield active (all further messages dropped)\n\n"
        f"Counter effectiveness: {o.get('effectiveness', 0)}%\n"
        "(calculated from confirmed actions only: block 50 · purge 30 · shield 20)"
    )


async def _notify(session, msg, report: ThreatReport, group: bool) -> None:
    text = format_report(msg, report, "group chat" if group else "private chat")
    targets = list(dict.fromkeys(f"{n}@s.whatsapp.net" for n in config.get().owner.numbers))
    if group:
        targets.append(msg.chat)
    for chat in targets:
        if chat == msg.chat and not group:
            continue  # never message the (now blocked) attacker
        try:
            await session.send_text(chat, text)
        except Exception as exc:  # noqa: BLE001
            logger.warn(f"antibug alert send failed: {exc}")


# ── main entry ──────────────────────────────────────────────────────────
async def inspect_message(session, msg) -> ThreatReport | None:
    """Scan one inbound message. Returns a ThreatReport when the pipeline must stop, else None."""
    number = msg.sender_number
    chat = str(msg.chat)
    group = _is_group(chat)
    settings = db.antibug_settings()
    text = msg.text or ""
    meta = msg.meta or {}

    # Compatibility: oversized payloads have always been logged, even when protection is paused.
    if not number:
        if len(text.encode("utf-8", "ignore")) > 20000:
            db.log_bug({"sender": msg.sender, "number": number, "from": chat, "reason": "oversized payload"})
        return None
    if not settings["enabled"]:
        return None
    exempt = _exempt(session, number)

    # 1) shield: already-countered attackers are dropped before anything else runs
    if not exempt and (db.antibug_shield_get(number) or number in _ACTIVE):
        if group and getattr(session, "delete_message", None):
            try:
                await session.delete_message(chat, msg.sender, msg.message_id)
            except Exception:  # noqa: BLE001
                pass
        elif not group and msg.message_id:
            fn = getattr(session, "delete_for_me", None)
            if callable(fn):
                try:
                    await fn(chat, msg.sender, msg.message_id)
                except Exception:  # noqa: BLE001
                    pass
        return ThreatReport(strength=100, handled=True)

    # 2) soft mute from earlier group incidents
    if group and not exempt and db.get_mute(chat, number):
        try:
            await session.delete_message(chat, msg.sender, msg.message_id)
        except Exception as exc:  # noqa: BLE001
            logger.warn(f"antibug muted-message delete failed: {exc}")
        return ThreatReport(strength=100, handled=True)

    # 3) analyse
    now = time.monotonic()
    key = (chat, number)
    window = _WINDOWS[key]
    window.append(now)
    while window and now - window[0] > WINDOW_SECONDS:
        window.popleft()
    fingerprint = _text_fingerprint(text)
    duplicates = _DUPLICATES[key]
    duplicates.append((now, fingerprint))
    while duplicates and now - duplicates[0][0] > 15:
        duplicates.popleft()
    track = _TRACK[number]
    track.append((now, chat, msg.sender, msg.message_id))
    while track and now - track[0][0] > TRACK_SECONDS:
        track.popleft()

    signals = analyze_payload(text, meta)
    limit = GROUP_BURST if group else DM_BURST
    if len(window) >= limit:
        signals.append(Signal("burst", "message flood", min(90, 60 + 4 * (len(window) - limit)), f"{len(window)} msgs / {WINDOW_SECONDS}s"))
    dup = sum(1 for _, fp in duplicates if fp == fingerprint) if fingerprint else 0
    if dup >= limit:
        signals.append(Signal("repeat", "repeated message", min(90, 60 + 4 * (dup - limit)), f"{dup} identical"))
    if meta.get("unparseable"):
        strikes = _UNPARSEABLE[number]
        strikes.append(now)
        while strikes and now - strikes[0] > 60:
            strikes.popleft()
        if len(strikes) >= 2:
            signals.append(Signal("unparseable-repeat", "repeated unparseable payloads", 70, f"{len(strikes)} in 60s"))
    report = ThreatReport(strength=combine([s.points for s in signals]), signals=signals)
    threshold = max(20, min(95, int(settings.get("threshold") or DEFAULT_THRESHOLD)))
    if report.strength < threshold:
        return None

    base_log = {"sender": msg.sender, "number": number, "from": chat, "reason": report.primary,
                "strength": report.strength, "signals": [s.label for s in report.signals]}

    # Claim the sender before any await so a burst of concurrent messages triggers exactly one counter.
    if number in _ACTIVE:
        return ThreatReport(strength=report.strength, handled=True)
    _ACTIVE[number] = now
    try:
        # 4) exempt senders are logged but never touched
        if exempt or (group and await _group_admin(session, chat, number)):
            db.log_bug({**base_log, "action": "exempt"})
            window.clear(); duplicates.clear()
            return None

        # 5) counter: block + purge run together for speed
        can_moderate = group and await _bot_admin(session, chat)
        refs = [(c, s, m) for _, c, s, m in list(track)]
        block_res, purge_res = await asyncio.gather(
            _try_block(session, msg) if settings["autoblock"] else asyncio.sleep(0, result=("skipped", "")),
            _purge(session, refs, can_moderate) if settings["autopurge"]
            else asyncio.sleep(0, result=(0, len(refs), "auto-purge is OFF")),
        )
        block, block_note = block_res
        purged, targets, purge_note = purge_res
        db.antibug_shield_add(number, report.strength, report.primary, block == "ok")
        if group and can_moderate and purged > 0:
            db.set_muted(chat, number, db.now_ms() + MUTE_SECONDS * 1000, "antibug flood guard")
        eff = effectiveness(block, purged, targets, True, settings)
        report.outcome = {"block": block, "block_note": block_note, "purged": purged, "targets": targets,
                          "purge_note": purge_note, "effectiveness": eff}
        report.countered = report.handled = True
        db.log_bug({**base_log, "action": "countered", "blocked": block == "ok", "deleted": purged,
                    "targets": targets, "effectiveness": eff})
        window.clear(); duplicates.clear(); track.clear()
        await _notify(session, msg, report, group)
        return report
    finally:
        _ACTIVE.pop(number, None)


def reset_runtime_state() -> None:
    _WINDOWS.clear()
    _DUPLICATES.clear()
    _TRACK.clear()
    _ACTIVE.clear()
    _UNPARSEABLE.clear()
    _GROUP_CACHE.clear()
