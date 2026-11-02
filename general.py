"""Everyday commands available to (almost) everyone."""
import json
import platform
import re
import sys
from datetime import datetime

from ... import api, config, db, format as fmt, runtime
from . import CommandError, command


def _sessions() -> list[str]:
    from .. import socket as wa  # lazy: socket imports the handler, which imports this package

    return wa.list_sessions()


@command("menu")
async def menu(ctx):
    """Legacy menu command retained for backward compatibility."""
    await ctx.reply(fmt.menu(ctx.sender_number))


@command("xcore")
async def xcore_startup(ctx):
    """Send XCORE identity image, terminal-style identity, soundtrack, then the command menu."""
    from pathlib import Path
    from datetime import datetime
    assets = Path(__file__).resolve().parents[2] / "assets"
    image = assets / "xcore-brand.png"
    soundtrack = assets / "xcore-id.mp3"
    send_image = getattr(ctx.session, "send_image", None)
    send_audio = getattr(ctx.session, "send_audio", None)
    if not image.is_file() or not soundtrack.is_file():
        raise CommandError("XCORE startup assets are missing from this installation.")
    if not callable(send_image) or not callable(send_audio):
        raise CommandError("This WhatsApp connection does not support the required startup media methods.")
    await send_image(ctx.chat, str(image), caption="XCORE® | WhatsApp Dev Bot\nAUTOMATE • CONTROL • DOMINATE")
    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    identity = (
        "╔══════════════════════════════════════╗\n"
        "║              XCORE®                  ║\n"
        "║       WHATSAPP DEV BOT               ║\n"
        "╚══════════════════════════════════════╝\n\n"
        "╭━━━〔 XCORE® 〕━━━╮\n"
        "┃ PROJECT SHΔDOW\n"
        "┃ Developer: DEV AUSTIN\n"
        "┃ Laboratory: SHΔDOWBORNE LΔB\n"
        "┃ Technology: XCORE®\n"
        f"┃ Date & Time: {now}\n"
        "╰━━━━━━━━━━━━━━━━━━╯\n"
        "JUST Δ LITTLE UNIMΔGINΔΔRY"
    )
    await ctx.reply(identity)
    await send_audio(ctx.chat, str(soundtrack), caption="XCORE® — Official Startup Soundtrack")
    await ctx.reply(fmt.menu(ctx.sender_number))


@command("botinfo")
async def botinfo(ctx):
    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    await ctx.reply("\n".join([
        "╭━━━〔 𝚇𝙲𝙾𝚁𝙴® 〕━━━╮",
        "",
        "PROJECT SHΔDOW",
        "Developer: 𝙳𝙴𝚅 𝙰𝚄𝚂𝚃𝙸𝙽",
        "Laboratory: SHΔDOWBORNE LΔB",
        "Technology: 𝚇𝙲𝙾𝚁𝙴®",
        f"Date & Time: {now}",
        "",
        "JUST Δ LITTLE UNIMΔGINΔΔRY",
    ]))


@command("ping")
async def ping(ctx):
    await ctx.reply("🏓 PONG")


@command("getid", "get-id")
async def getid(ctx):
    await ctx.reply(f"🆔 Chat: {ctx.chat}\n👤 Sender: {ctx.sender}\n📱 Number: +{ctx.sender_number}")


@command("device")
async def device(ctx):
    """Inspect linked-device metadata exposed by the WhatsApp library (not server OS)."""
    import re
    target = re.sub(r"\D", "", ctx.args[0] if ctx.args else ctx.sender_number)
    if not 7 <= len(target) <= 15:
        raise CommandError("Usage: ®device [international-number]")
    method = getattr(ctx.session, "user_devices", None)
    result = None
    if callable(method):
        try:
            result = await method(target)
        except Exception as exc:
            result = f"Unavailable: {str(exc)[:180]}"
    host = f"{platform.system()} {platform.release()} / Python {platform.python_version()}"
    payload = pretty_json(result, 1800) if result is not None else "WhatsApp did not expose linked-device metadata."
    await ctx.reply(
        "╭━━━〔 𝚇𝙲𝙾𝚁𝙴® 〕━━━╮\n\n"
        "📱 DEVICE / CLIENT INSPECTION\n"
        f"Target: +{target}\n"
        f"Linked-device data: {payload}\n"
        f"Bot host runtime: {host}\n\n"
        "The host runtime is not the target's phone OS. Android/iOS is reported only when WhatsApp exposes reliable metadata."
    )


@command("clientid", "client-id")
async def clientid(ctx):
    target = re.sub(r"\D", "", ctx.args[0] if ctx.args else ctx.sender_number)
    if not 7 <= len(target) <= 15:
        raise CommandError("Usage: ®clientid [international-number]")
    method = getattr(ctx.session, "user_devices", None)
    if not callable(method):
        raise CommandError("Client/device identifiers are not exposed by this connection.")
    try:
        result = await method(target)
    except Exception as exc:
        raise CommandError(f"Client ID lookup unavailable: {str(exc)[:180]}") from None
    await ctx.reply(f"🆔 CLIENT IDENTIFIERS\nTarget: +{target}\n{pretty_json(result, 2500)}")


@command("clientcheck", "client-check")
async def clientcheck(ctx):
    target = re.sub(r"\D", "", ctx.args[0] if ctx.args else ctx.sender_number)
    if not 7 <= len(target) <= 15:
        raise CommandError("Usage: ®clientcheck [international-number]")
    checker = getattr(ctx.session, "is_on_whatsapp", None)
    if not callable(checker):
        raise CommandError("WhatsApp client verification is unavailable in this library version.")
    try:
        result = await checker(target)
    except Exception as exc:
        raise CommandError(f"Client check failed: {str(exc)[:180]}") from None
    await ctx.reply(f"🔎 CLIENT CHECK\nTarget: +{target}\nResult: {pretty_json(result, 1800)}")


@command("session")
async def session(ctx):
    s = _sessions()
    await ctx.reply(f"🔗 Active sessions: {len(s)}\n" + ("\n".join(f"• +{x}" for x in s) or "None"))


@command("stats")
async def stats(ctx):
    s = db.get_stats()
    await ctx.reply(f"📊 STATS\nChecks: {s['checks']}\nBanned: {s['banned']}\nSafe: {s['safe']}")


@command("uptime")
async def uptime(ctx):
    await ctx.reply(f"⏱ UPTIME\n{runtime.uptime_seconds()} seconds")


@command("support")
async def support(ctx):
    await ctx.reply(f"🛠 SUPPORT\nContact: +{config.get().owner.number}")


@command("buypremium")
async def buypremium(ctx):
    await ctx.reply(f"💎 PREMIUM\nContact +{config.get().owner.number}")


@command("checkaccess")
async def checkaccess(ctx):
    await ctx.reply(fmt.access_card(ctx.sender_number))


@command("bancheck")
async def bancheck(ctx):
    try:
        phone = api.normalize_phone(ctx.args[0] if ctx.args else "")
    except ValueError as e:
        raise CommandError(str(e)) from None
    result = await api.check_ban(phone)
    db.bump_stats(result["banned"])
    await ctx.reply(fmt.ban_card(phone, result, ctx.sender_number))


@command("api")
async def api_info(ctx):
    from .devtools import LAB_METHODS, apilab

    if ctx.args and ctx.args[0].lower() in LAB_METHODS:
        return await apilab(ctx)
    cfg = config.get().api
    await ctx.reply(f"🌐 API\n{cfg.url}\nMethod: {cfg.method}")


@command("health")
async def health(ctx):
    cfg = config.get()
    s, mons = db.get_stats(), db.list_monitors()
    up = sum(1 for x in mons if x.get("status") == "up")
    down = sum(1 for x in mons if x.get("status") == "down")
    lines = [
        "╭──〔 𝗛𝗘𝗔𝗟𝗧𝗛 〕",
        f"│ Bot:       {cfg.bot.name}",
        f"│ Mode:      {db.get_mode()}",
        f"│ Uptime:    {runtime.uptime_seconds()}s",
        f"│ Sessions:  {len(_sessions())}",
        f"│ Checks:    {s['checks']} ({s['banned']} banned / {s['safe']} safe)",
        f"│ Monitors:  {len(mons)} (🟢 {up} / 🔴 {down})",
        "╰──────────────",
        fmt.footer(ctx.sender_number),
    ]
    await ctx.reply("\n".join(lines))


@command("services")
async def services(ctx):
    mons = db.list_monitors()
    if not mons:
        return await ctx.reply("No services monitored.")
    lines = ["╭──〔 𝗦𝗘𝗥𝗩𝗜𝗖𝗘𝗦 〕", ""]
    for x in mons:
        icon = {"up": "🟢", "down": "🔴"}.get(x.get("status"), "⚪")
        lines += [f"│ {icon} {x['url']}", f"│    checks: {x.get('totalChecks', 0)}  downs: {x.get('totalDown', 0)}"]
        if x.get("downSince"):
            lines.append(f"│    down for: {(db.now_ms() - x['downSince']) // 1000}s")
        lines.append("")
    lines += ["╰──────────────", fmt.footer(ctx.sender_number)]
    await ctx.reply("\n".join(lines))


def pretty_json(data, limit: int = 3500) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, default=str)[:limit]
