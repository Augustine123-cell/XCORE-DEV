"""Owner / sudo management commands."""
import asyncio
import os

from ... import config, db
from ..context import Incoming
from . import CommandError, command, require_owner, require_sudo


def _to_int(v, default: int) -> int:
    """JS-style `Number(v) || default` (missing, invalid or 0 → default)."""
    try:
        return int(float(v)) or default
    except (TypeError, ValueError):
        return default


def _target_number(ctx) -> str:
    q = ctx.msg.quoted_participant
    n = q.split("@")[0].split(":")[0] if q else (ctx.args[0] if ctx.args else "")
    if not db.clean(n):
        raise CommandError("Reply to a user or provide a number.")
    return n


def _need_number(value, usage: str) -> str:
    if not value or not db.clean(value):
        raise CommandError(usage)
    return value


@command("sudo")
async def sudo(ctx):
    require_owner(ctx)
    db.add_sudo(_target_number(ctx))
    await ctx.reply("⚔️ Sudo added.")


@command("delsudo")
async def delsudo(ctx):
    require_owner(ctx)
    db.remove_sudo(_target_number(ctx))
    await ctx.reply("⚔️ Sudo removed.")


@command("self")
async def self_mode(ctx):
    require_sudo(ctx)
    db.set_mode("self")
    await ctx.reply("🔒 Self mode enabled.")


@command("public")
async def public_mode(ctx):
    require_sudo(ctx)
    db.set_mode("public")
    await ctx.reply("🌐 Public mode enabled.")


MODES = {
    "self": "👑 Owner + ⚔️ Sudo only",
    "public": "🌐 Public commands enabled",
    "dev": "🛠 Owner-only. Others are silent.",
    "maintenance": "🔧 Only health/menu/support/checkaccess respond.",
    "stealth": "🕶 Owner-only and silent to everyone else.",
}


@command("mode")
async def mode(ctx):
    from ...format import footer

    if not ctx.is_owner:
        return await ctx.reply("⛔ Owner only.")
    target = (ctx.args[0] if ctx.args else "").lower()
    if not target:
        lines = ["✦ 𝗕𝗢𝗧 𝗠𝗢𝗗𝗘", "", f"Current: *{db.get_mode()}*", ""]
        for k, v in MODES.items():
            lines.append(f"• ®mode {k}\n   {v}")
        lines.append(footer(ctx.sender_number))
        return await ctx.reply("\n".join(lines))
    if target not in MODES:
        return await ctx.reply(f"❌ Unknown mode. Use: {', '.join(MODES)}")
    db.set_mode(target)
    await ctx.reply(f"✅ Mode → *{target}*\n{MODES[target]}{footer(ctx.sender_number)}")


@command("restart")
async def restart(ctx):
    require_owner(ctx)
    await ctx.reply("♻️ Restarting…")
    # Exit; the supervisor (Docker restart policy / PM2 / Railway / systemd) starts a fresh process.
    asyncio.get_running_loop().call_later(0.5, os._exit, 0)


@command("premium")
async def premium(ctx):
    require_owner(ctx)
    a = (ctx.args[0] if ctx.args else "").lower()
    if a == "add":
        n = _need_number(ctx.args[1] if len(ctx.args) > 1 else None, "Usage: ®premium add <number> <days>")
        db.add_premium(n, _to_int(ctx.args[2] if len(ctx.args) > 2 else None, 30), "standard", ctx.sender_number)
        return await ctx.reply("💎 Premium added.")
    if a == "remove":
        db.remove_premium(_need_number(ctx.args[1] if len(ctx.args) > 1 else None, "Usage: ®premium remove <number>"))
        return await ctx.reply("💎 Premium removed.")
    if a == "list":
        rows = [f"+{x['number']} • {x['plan']} • {x['daysLeft']} days" for x in db.list_premium()]
        return await ctx.reply("\n".join(rows) or "None")
    raise CommandError("Usage: ®premium add|remove|list")


@command("ban")
async def ban(ctx):
    # In groups, ®ban removes the target from that group. In private chat it retains
    # the legacy local access-ban behaviour; use ®localban explicitly if desired.
    if str(ctx.chat).endswith("@g.us"):
        return await _group_remove(ctx)
    require_owner(ctx)
    if not ctx.args:
        raise CommandError("Usage: ®ban <number> <days|0> [reason]")
    n = ctx.args[0]
    days = _to_int(ctx.args[1] if len(ctx.args) > 1 else None, 0)
    reason = " ".join(ctx.args[2:]) or "No reason"
    db.ban_user(n, days, reason, ctx.sender_number)
    await ctx.reply("🚫 Local ban added to User.")


@command("localban")
async def localban(ctx):
    require_owner(ctx)
    if not ctx.args:
        raise CommandError("Usage: ®localban <number> <days|0> [reason]")
    n = ctx.args[0]
    days = _to_int(ctx.args[1] if len(ctx.args) > 1 else None, 0)
    reason = " ".join(ctx.args[2:]) or "No reason"
    db.ban_user(n, days, reason, ctx.sender_number)
    await ctx.reply("🚫 Local ban added to User.")


@command("unban")
async def unban(ctx):
    require_owner(ctx)
    if not ctx.args:
        raise CommandError("Usage: ®unban <number>")
    db.unban_user(ctx.args[0])
    await ctx.reply("✅ Local ban removed.")


@command("remove")
async def remove(ctx):
    require_owner(ctx)
    if not ctx.args:
        raise CommandError("Usage: ®remove <number>")
    db.remove_user(ctx.args[0])
    await ctx.reply("⛔ User removed from local bot access.")


@command("restore")
async def restore(ctx):
    require_owner(ctx)
    if not ctx.args:
        raise CommandError("Usage: ®restore <number>")
    db.restore_user(ctx.args[0])
    await ctx.reply("✅ User access restored.")


@command("banned")
async def banned(ctx):
    require_owner(ctx)
    rows = [f"+{x['number']} • {x['daysLeft']} • {x['reason']}" for x in db.list_banned()]
    await ctx.reply("\n".join(rows) or "No locally banned users.")


@command("antiban")
async def antiban(ctx):
    require_sudo(ctx)
    v = (ctx.args[0] if ctx.args else "").lower()
    if v not in ("on", "off"):
        return await ctx.reply(f"🛡 Antiban: {'ON' if db.get_antiban() else 'OFF'}")
    db.set_antiban(v == "on")
    await ctx.reply(f"🛡 Antiban {v.upper()}.")


@command("antibug")
async def antibug(ctx):
    """®antibug [log|status|on|off|block on/off|purge on/off|level low/normal/high|trust N|untrust N|unblock N|list]"""
    require_sudo(ctx)
    from datetime import datetime, timezone

    sub = (ctx.args[0] if ctx.args else "log").lower()
    arg = ctx.args[1] if len(ctx.args) > 1 else ""
    cfg = db.antibug_settings()
    onoff = lambda v: "ON" if v else "OFF"  # noqa: E731
    levels = {"low": 75, "normal": 60, "high": 45}

    if sub in ("on", "off"):
        db.set_antibug_option("enabled", sub == "on")
        return await ctx.reply(f"🛡 Antibug protection {sub.upper()}.")
    if sub in ("block", "purge"):
        if arg.lower() not in ("on", "off"):
            return await ctx.reply(f"🛡 Auto-{sub}: {onoff(cfg['auto' + sub])}. Use ®antibug {sub} on|off")
        db.set_antibug_option("auto" + sub, arg.lower() == "on")
        return await ctx.reply(f"🛡 Auto-{sub} {arg.upper()}.")
    if sub == "level":
        if arg.lower() not in levels:
            current = next((k for k, v in levels.items() if v == cfg["threshold"]), f"custom ({cfg['threshold']})")
            return await ctx.reply(f"🛡 Sensitivity: {current}. Use ®antibug level low|normal|high")
        db.set_antibug_option("threshold", levels[arg.lower()])
        return await ctx.reply(f"🛡 Sensitivity set to {arg.lower()} (counter at strength ≥ {levels[arg.lower()]}).")
    if sub in ("trust", "untrust"):
        number = db.clean(_need_number(arg, f"Usage: ®antibug {sub} <number>"))
        db.antibug_trust(number, sub == "trust")
        return await ctx.reply(f"🛡 +{number} {'trusted — never scanned for countermeasures' if sub == 'trust' else 'no longer trusted'}.")
    if sub == "unblock":
        number = db.clean(_need_number(arg, "Usage: ®antibug unblock <number>"))
        was_shielded = db.antibug_shield_remove(number)
        note = ""
        fn = getattr(ctx.session, "unblock_contact", None)
        if callable(fn):
            try:
                await fn(f"{number}@s.whatsapp.net")
                note = " WhatsApp block lifted."
            except Exception as exc:  # noqa: BLE001
                note = f" (WhatsApp unblock failed: {exc})"
        return await ctx.reply(f"🛡 +{number} {'removed from the shield' if was_shielded else 'was not in the shield'}.{note}")
    if sub == "list":
        rows = [f"+{n} • {v.get('strength', '?')}/100 • {v.get('reason')} • {'WA-blocked' if v.get('waBlocked') else 'bot-shield only'}"
                for n, v in db.antibug_shield_list().items()]
        return await ctx.reply("\n".join(rows) or "Shield is empty.")
    if sub == "status":
        bugs = db.list_bugs(200)
        countered = [b for b in bugs if b.get("action") == "countered"]
        avg = round(sum(b.get("effectiveness", 0) for b in countered) / len(countered)) if countered else None
        level = next((k for k, v in levels.items() if v == cfg["threshold"]), f"custom ({cfg['threshold']})")
        return await ctx.reply(
            "🛡 ANTIBUG STATUS\n\n"
            f"Protection: {onoff(cfg['enabled'])}\nAuto-block: {onoff(cfg['autoblock'])}\nAuto-purge: {onoff(cfg['autopurge'])}\n"
            f"Sensitivity: {level}\nShielded attackers: {len(cfg['blocked'])}\nTrusted numbers: {len(cfg['trusted'])}\n"
            f"Attacks countered (last 200 events): {len(countered)}\n"
            f"Average effectiveness: {'n/a' if avg is None else f'{avg}%'}"
        )

    def row(x):
        stamp = datetime.fromtimestamp(x["at"] / 1000, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        extra = ""
        if "strength" in x:
            extra = f" • {x['strength']}/100"
            if x.get("action") == "countered":
                extra += f" • effectiveness {x.get('effectiveness', 0)}%"
            elif x.get("action"):
                extra += f" • {x['action']}"
        return f"{stamp} • {x.get('sender')} • {x.get('reason')}{extra}"

    await ctx.reply("\n".join(row(x) for x in db.list_bugs(10)) or "No alerts.")


# ── WhatsApp group administration ──────────────────────────────────────
import re
import time
from ...whatsapp.group_utils import participants, participant_jid, number_from_jid, is_admin, is_superadmin, find_participant

_PENDING_SWEEPS: dict[str, tuple[float, list[str]]] = {}


def _target_group_number(ctx) -> str:
    quoted = ctx.msg.quoted_participant
    raw = quoted.split("@")[0].split(":")[0] if quoted else (ctx.args[0] if ctx.args else "")
    number = re.sub(r"\D", "", raw)
    if not 7 <= len(number) <= 15:
        raise CommandError("Reply to a member or provide an international phone number.")
    return number


async def _group_metadata(ctx):
    if not str(ctx.chat).endswith("@g.us"):
        raise CommandError("This command must be used inside a WhatsApp group.")
    method = getattr(ctx.session, "group_info", None)
    if not callable(method):
        raise CommandError("Group metadata is unavailable in this WhatsApp connection.")
    try:
        info = await method(ctx.chat)
    except Exception as exc:
        raise CommandError(f"Could not retrieve group information: {str(exc)[:180]}") from None
    return info


async def _require_bot_admin(ctx, info):
    bot_member = find_participant(info, ctx.session.number)
    if not bot_member or not is_admin(bot_member):
        raise CommandError("The connected WhatsApp account must be a group administrator.")


async def _require_group_operator(ctx, info, owner_only=False):
    await _require_bot_admin(ctx, info)
    if owner_only:
        require_owner(ctx)
        return
    if ctx.is_owner or ctx.is_sudo:
        return
    member = find_participant(info, ctx.sender_number)
    if not member or not is_admin(member):
        raise CommandError("Only the bot owner, SUDO, or a group administrator can use this command.")


async def _change_participant(ctx, number: str, action: str):
    info = await _group_metadata(ctx)
    await _require_group_operator(ctx, info, owner_only=(action == "remove_admin"))
    target = find_participant(info, number)
    if not target:
        raise CommandError("Target is not a member of this group.")
    if is_superadmin(target):
        raise CommandError("The group creator cannot be modified by the bot.")
    if number in config.get().owner.numbers and action in {"remove", "demote"}:
        raise CommandError("Registered XCORE owner numbers are protected.")
    if number == ctx.session.number:
        raise CommandError("The bot's own account is protected.")
    api_action = {"promote": "promote", "demote": "demote", "remove": "remove"}[action]
    try:
        await ctx.session.group_participants(ctx.chat, [number], api_action)
    except Exception as exc:
        raise CommandError(f"WhatsApp rejected the operation: {str(exc)[:200]}") from None
    return number


async def _group_remove(ctx):
    number = _target_group_number(ctx)
    await _change_participant(ctx, number, "remove")
    await ctx.reply(f"🚫 Removed +{number} from this group.")


@command("ordain", "promote")
async def ordain(ctx):
    number = _target_group_number(ctx)
    await _change_participant(ctx, number, "promote")
    await ctx.reply(f"👑 +{number} has been promoted to group administrator.")


@command("strip", "demote")
async def strip(ctx):
    number = _target_group_number(ctx)
    await _change_participant(ctx, number, "demote")
    await ctx.reply(f"⚔️ Administrator privileges removed from +{number}.")


@command("jid")
async def jid(ctx):
    await _group_metadata(ctx)
    await ctx.reply(f"🆔 GROUP JID\n{ctx.chat}")


@command("purge")
async def purge(ctx):
    info = await _group_metadata(ctx)
    await _require_group_operator(ctx, info, owner_only=True)
    targets = []
    for member in participants(info):
        number = number_from_jid(participant_jid(member))
        if not number or not is_admin(member) or is_superadmin(member):
            continue
        if number == ctx.session.number or number in config.get().owner.numbers:
            continue
        targets.append(number)
    if not targets:
        return await ctx.reply("✅ No eligible administrators to demote. The group creator and protected owners are untouched.")
    done, failed = 0, []
    for number in targets:
        try:
            await ctx.session.group_participants(ctx.chat, [number], "demote")
            done += 1
        except Exception:
            failed.append(number)
    await ctx.reply(f"⚔️ ADMIN PURGE COMPLETE\nDemoted: {done}\nFailed: {len(failed)}\nProtected: group creator, bot and registered owners.")


@command("sweepclean")
async def sweepclean(ctx):
    require_owner(ctx)
    info = await _group_metadata(ctx)
    await _require_bot_admin(ctx, info)
    key = ctx.chat
    if not ctx.args or ctx.args[0].lower() != "confirm":
        targets = []
        for member in participants(info):
            number = number_from_jid(participant_jid(member))
            if not number or number == ctx.session.number or number in config.get().owner.numbers:
                continue
            # Keep group admins by default so a cleanup cannot unexpectedly strip the
            # group's leadership; group creator is never removable.
            if is_admin(member) or is_superadmin(member):
                continue
            targets.append(number)
        _PENDING_SWEEPS[key] = (time.monotonic() + 60, targets)
        return await ctx.reply(
            "⚠️ SWEEPCLEAN PREVIEW\n"
            f"Eligible members to remove: {len(targets)}\n"
            "Protected: bot, registered XCORE owners, group administrators and group creator.\n\n"
            "To proceed within 60 seconds, send ®sweepclean confirm."
        )
    pending = _PENDING_SWEEPS.get(key)
    if not pending or pending[0] < time.monotonic():
        _PENDING_SWEEPS.pop(key, None)
        raise CommandError("No active sweep confirmation. Run ®sweepclean to preview first.")
    _PENDING_SWEEPS.pop(key, None)
    targets = pending[1]
    done, failed = 0, []
    for number in targets:
        try:
            await ctx.session.group_participants(ctx.chat, [number], "remove")
            done += 1
        except Exception:
            failed.append(number)
    await ctx.reply(f"🧹 SWEEPCLEAN COMPLETE\nRemoved: {done}\nFailed: {len(failed)}\nProtected admins, owners and bot were retained.")


@command("expel")
async def expel(ctx):
    """Remove the replied-to group member (or supplied number), with normal admin protections."""
    await _group_remove(ctx)
