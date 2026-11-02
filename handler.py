"""Inbound message pipeline: antibug scan → name capture → prefix → role/mode gating → dispatch."""
from .. import config, db, logger
from .commands import REGISTRY, CommandError
from .security import inspect_message
from .context import Ctx, Incoming, Session, norm_jid

# Commands that need premium (or sudo/owner).
OSINT = {"groups", "wafind", "agecheck", "getwadetail", "getgcdetail", "tgfind", "tgage", "tgdetail", "tggcdetail"}
# What non-sudo users may run while the bot is in `self` mode.
PUBLIC_ONLY = {"xcore", "menu", "ping", "uptime", "support", "buypremium", "checkaccess", "health", "services", "botinfo"}
MAINTENANCE_OK = {"health", "xcore", "menu", "support", "checkaccess", "mode", "botinfo"}


async def handle_message(session: Session, msg: Incoming) -> None:
    cfg = config.get()

    # Anti-bug runs FIRST so a malicious message is countered before anything else touches it.
    try:
        threat = await inspect_message(session, msg)
    except Exception as e:  # noqa: BLE001
        logger.error("antibug scan:", e)
        threat = None
    if threat is not None and threat.handled:
        return

    if msg.push_name:
        try:
            db.save_name(norm_jid(msg.sender), msg.push_name)
        except Exception:  # noqa: BLE001
            pass

    text = msg.text or ""
    prefix = next((item for item in sorted(cfg.prefixes, key=len, reverse=True) if text.startswith(item)), None)
    if prefix is None:
        return
    parts = text[len(prefix):].strip().split()
    if not parts:
        return
    cmd, args = parts[0].lower(), parts[1:]
    handler = REGISTRY.get(cmd)
    if not handler:
        return

    number = msg.sender_number
    is_owner = number in cfg.owner.numbers
    is_sudo = db.is_sudo(number)
    mode = db.get_mode()

    if (db.is_removed(number) or db.is_banned(number)) and not is_owner:
        from ..format import restricted_card
        await session.send_text(msg.chat, restricted_card("Your account is banned or removed from XCORE."), quoted=msg.raw)
        return
    if mode in ("dev", "stealth") and not is_owner:
        return
    if mode == "maintenance" and not is_owner and cmd not in MAINTENANCE_OK:
        return
    if mode == "self" and not is_owner and not is_sudo and cmd not in PUBLIC_ONLY and cmd != "mode":
        from ..format import restricted_card
        await session.send_text(msg.chat, restricted_card("This feature is restricted to the owner and SUDO users."), quoted=msg.raw)
        return

    ctx = Ctx(session=session, msg=msg, args=args, is_owner=is_owner, is_sudo=is_sudo, cmd=cmd)
    if cmd in OSINT and not (is_owner or is_sudo or db.is_premium(number)):
        from ..format import restricted_card
        await ctx.reply(restricted_card("Premium-only feature. Use ®buypremium to request access."))
        return
    try:
        await handler(ctx)
    except CommandError as e:
        await ctx.reply(f"❌ {e}")
    except Exception as e:  # noqa: BLE001 — report any failure to the chat, keep the bot alive
        logger.error(f"/{cmd}:", e)
        try:
            await ctx.reply(f"❌ {e}")
        except Exception:  # noqa: BLE001
            pass
