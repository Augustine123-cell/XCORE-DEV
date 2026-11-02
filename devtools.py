"""Sudo/dev tooling: monitor, watch, apilab, scan."""
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import httpx

from ... import api, config

from ... import clans, db
from ...format import footer
from ...urlguard import UrlBlocked, assert_safe_url
from ..context import norm_jid
from . import command

USER_AGENT = "Shadowborne/5.0"
ALLOWED_METHODS = {"get", "post", "put", "delete", "patch", "head", "options"}
LAB_METHODS = ALLOWED_METHODS | {"headers", "status"}


def _is_sudo(ctx) -> bool:
    return ctx.is_owner or ctx.is_sudo


# ── .monitor ────────────────────────────────────────────────────────────
@command("monitor")
async def monitor(ctx):
    if not _is_sudo(ctx):
        return await ctx.reply("⛔ Sudo only.")
    sub = (ctx.args[0] if ctx.args else "").lower()
    if sub == "list" or not ctx.args:
        mons = db.list_monitors()
        if not mons:
            return await ctx.reply("No monitors. Add with: ®monitor <url>")
        lines = ["✦ 𝗠𝗢𝗡𝗜𝗧𝗢𝗥𝗦", ""]
        for i, x in enumerate(mons, 1):
            icon = {"up": "🟢", "down": "🔴"}.get(x.get("status"), "⚪")
            lines.append(f"{i}. {icon} {x['url']}")
            if x.get("lastCheck"):
                t = datetime.fromtimestamp(x["lastCheck"] / 1000, timezone.utc).strftime("%H:%M:%S")
                lines.append(f"   Last check: {t}")
        lines.append(footer(ctx.sender_number))
        return await ctx.reply("\n".join(lines))
    if sub == "remove":
        if len(ctx.args) < 2:
            return await ctx.reply("Usage: ®monitor remove <url>")
        db.remove_monitor(ctx.args[1])
        return await ctx.reply(f"🗑 Removed monitor: {ctx.args[1]}{footer(ctx.sender_number)}")
    url = ctx.args[0]
    try:
        await assert_safe_url(url)
    except UrlBlocked as e:
        return await ctx.reply(f"❌ {e}")
    if db.add_monitor(url, ctx.chat, ctx.sender_number):
        await ctx.reply(f"✅ Monitoring: {url}\nAlerts will be sent here.{footer(ctx.sender_number)}")
    else:
        await ctx.reply(f"⚠️ Already monitoring: {url}{footer(ctx.sender_number)}")


# ── .watch ──────────────────────────────────────────────────────────────
@command("watch")
async def watch(ctx):
    if not _is_sudo(ctx):
        return await ctx.reply("⛔ Sudo only.")
    if not ctx.args:
        return await ctx.reply("Usage: ®watch <url>")
    url = ctx.args[0]
    try:
        await assert_safe_url(url)
    except UrlBlocked as e:
        return await ctx.reply(f"❌ {e}")
    t = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as http:
            res = await http.get(url)
        line = f"│ Status: {res.status_code}"
    except Exception as e:  # noqa: BLE001
        line = f"│ Error: {e}"
    latency = int((time.monotonic() - t) * 1000)
    await ctx.reply(
        "\n".join(["╭──〔 𝗪𝗔𝗧𝗖𝗛 〕", f"│ URL: {url}", line, f"│ Latency: {latency} ms", "╰──────────────", footer(ctx.sender_number)])
    )


# ── .apilab ─────────────────────────────────────────────────────────────
@command("apilab")
async def apilab(ctx):
    if not _is_sudo(ctx):
        return await ctx.reply("⛔ Sudo only.")
    method = (ctx.args[0] if ctx.args else "").lower()
    url = ctx.args[1] if len(ctx.args) > 1 else None
    if not method or not url:
        return await ctx.reply(
            "\n".join(
                [
                    "✦ 𝗔𝗣𝗜 𝗟𝗔𝗕𝗢𝗥𝗔𝗧𝗢𝗥𝗬",
                    "",
                    "• ®apilab get <url>",
                    "• ®apilab post <url>",
                    "• ®apilab headers <url>",
                    "• ®apilab status <url>",
                    "• ®apilab put/delete/patch <url>",
                ]
            )
        )
    if method not in LAB_METHODS:
        return await ctx.reply(f"❌ Unsupported method: {method}")
    try:
        await assert_safe_url(url)
    except UrlBlocked as e:
        return await ctx.reply(f"❌ {e}")

    real = "get" if method in ("headers", "status") else method
    head = ["╭───〔 𝗔𝗣𝗜 𝗜𝗡𝗦𝗣𝗘𝗖𝗧𝗢𝗥 〕"]
    tail = ["╰────────────────────", footer(ctx.sender_number)]
    t = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False, headers={"User-Agent": USER_AGENT}) as http:
            res = await http.request(real.upper(), url)
        latency = int((time.monotonic() - t) * 1000)
        body = [
            f"│ Status:  {res.status_code} {res.reason_phrase or ''}".rstrip(),
            f"│ Method:  {method.upper()}",
            f"│ URL:     {url}",
            f"│ Latency: {latency}ms",
            f"│ Type:    {res.headers.get('content-type', 'unknown')}",
            f"│ Size:    {len(res.content) / 1024:.2f} KB",
            f"│ Server:  {res.headers.get('server', '—')}",
        ]
    except Exception as e:  # noqa: BLE001
        body = [f"│ URL: {url}", f"│ Method: {method.upper()}", f"│ Error: {e}"]
    await ctx.reply("\n".join(head + body + tail))


# ── .api-test ───────────────────────────────────────────────────────────
@command("api-test")
async def api_test(ctx):
    """Test the configured ban API once, using a dummy number rather than a real target."""
    if not _is_sudo(ctx):
        return await ctx.reply("⛔ Sudo only.")
    cfg = config.get().api
    parts = urlsplit(cfg.url)
    safe_endpoint = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    started = time.monotonic()
    try:
        result = await api.check_ban("0000000000")
        elapsed = int((time.monotonic() - started) * 1000)
        value = result.get("banned") if isinstance(result, dict) else None
        detail = "Provider returned: banned" if value is True else "Provider returned: not banned" if value is False else "Provider responded; result format is unrecognized"
        await ctx.reply("\n".join([
            "🔌 𝚇𝙲𝙾𝚁𝙴® API TEST", "━━━━━━━━━━━━━━━━━━━━",
            f"Endpoint: {safe_endpoint}", f"Method: {cfg.method}",
            "Status: REACHABLE", f"Latency: {elapsed} ms", detail,
            "━━━━━━━━━━━━━━━━━━━━",
        ]))
    except Exception as e:  # noqa: BLE001 — API failures must not break command processing
        elapsed = int((time.monotonic() - started) * 1000)
        await ctx.reply("\n".join([
            "🔌 𝚇𝙲𝙾𝚁𝙴® API TEST", "━━━━━━━━━━━━━━━━━━━━",
            f"Endpoint: {safe_endpoint}", "Status: FAILED", f"Latency: {elapsed} ms",
            f"Error: {type(e).__name__}: {str(e)[:240]}",
            "Check the API URL, credentials, and provider status.",
        ]))


# ── .scan ────────────────────────────────────────────────────────────────
@command("scan", "identify")
async def scan(ctx):
    if ctx.cmd != "identify" and not _is_sudo(ctx):
        return await ctx.reply("⛔ Sudo only. The public clan check is ®identify.")
    sub = (ctx.args[0] if ctx.args else "").lower()
    sn = ctx.sender_number

    if sub == "list":
        items = clans.list_clans()
        lines = [f"✦ 𝗖𝗟𝗔𝗡 𝗗𝗔𝗧𝗔𝗕𝗔𝗦𝗘 — {len(items)} entries", ""]
        for i, c in enumerate(items[:50], 1):
            lines.append(f"{i}. {c.get('name')}\n   Tag: {c.get('tag')}")
        if len(items) > 50:
            lines.append(f"… +{len(items) - 50} more")
        lines.append(footer(sn))
        return await ctx.reply("\n".join(lines))

    if sub == "add" and ctx.is_owner:
        payload = [s.strip() for s in " ".join(ctx.args[1:]).split("|")]
        if len(payload) < 2:
            return await ctx.reply("Usage: ®scan add <name> | <tag> | <description>")
        name, tag, *rest = payload
        try:
            clans.add_clan({"name": name, "tag": tag, "description": rest[0] if rest else "", "members": 0, "age": "unknown", "threat": "UNKNOWN"})
        except ValueError as e:
            return await ctx.reply(f"❌ {e}")
        return await ctx.reply(f"✅ Added clan: {name} ({tag}){footer(sn)}")

    quoted = ctx.msg.quoted_participant
    if quoted:
        label = db.get_name(norm_jid(quoted)) or "Unknown"
    elif ctx.args:
        num = db.clean(ctx.args[0])
        if not num:
            return await ctx.reply("❌ Invalid number.")
        label = db.get_name(f"{num}@s.whatsapp.net") or f"+{num}"
    else:
        return await ctx.reply("Usage:\n®identify (reply)\n®identify <number>\n®scan list\n®scan add <name> | <tag> | <desc>")

    matches = clans.match_name(label)
    if not matches:
        return await ctx.reply(
            "\n".join(
                [
                    "╭──〔 𝗦𝗛𝗔𝗗𝗢𝗪 𝗦𝗖𝗔𝗡 〕",
                    f"│ Target: {label}",
                    "│ Result: ❌ NOT FOUND",
                    "│ No known clan tag matched this display name.",
                    "╰──────────────",
                    footer(sn),
                ]
            )
        )
    c, conf = matches[0], clans.confidence(matches)
    members = c.get("members")
    await ctx.reply(
        "\n".join(
            [
                "╭──〔 𝗦𝗛𝗔𝗗𝗢𝗪 𝗦𝗖𝗔𝗡 〕",
                f"│ Target      : {label}",
                f"│ Clan name   : {c.get('name')}",
                f"│ Clan tag    : {c.get('tag')}",
                f"│ member count: {'unknown' if members is None else members}",
                f"│ description : {c.get('description') or '—'}",
                f"│ Confidence  : {conf}%",
                f"│ clan age    : {c.get('age') or 'unknown'}",
                f"│ Threat      : {c.get('threat') or 'UNKNOWN'}",
                "╰──────────────",
                footer(sn),
            ]
        )
    )
