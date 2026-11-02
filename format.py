"""Message cards and the menu."""
import re

from .db import clean, get_mode, get_role, list_banned, list_premium

BANNER = ["╭━━━〔 𝚇𝙲𝙾𝚁𝙴® 〕━━━╮"]

BRAND_HEADER = "╭━━━〔 𝚇𝙲𝙾𝚁𝙴® 〕━━━╮"
BRAND_FOOTER = "╰━━━━━━━━━━━━━━━━━━╯\nDeveloper: 𝙳𝙴𝚅 𝙰𝚄𝚂𝚃𝙸𝙽\nSHΔDOWBORNE LΔB 𝚇𝙲𝙾𝚁𝙴®"


def brand_message(text: str) -> str:
    """Apply the official XCORE header/footer once to every outgoing bot message."""
    value = str(text or "").strip()
    if not value.startswith(BRAND_HEADER):
        value = f"{BRAND_HEADER}\n\n{value}" if value else BRAND_HEADER
    if "Developer: 𝙳𝙴𝚅 𝙰𝚄𝚂𝚃𝙸𝙽" not in value:
        value = f"{value.rstrip()}\n\n{BRAND_FOOTER}"
    # Existing command-specific footers already contain the creator attribution.
    # Do not append a second footer to menus/cards that include their own role details.
    return value

_BADGES = {
    "OWNER": "👑 OWNER",
    "SUDO": "⚔️ SUDO",
    "PREMIUM": "💎 PREMIUM",
    "PAIRED": "🔗 PAIRED",
    "USER": "👤 USER",
    "BANNED": "🚫 BANNED",
    "REMOVED": "⛔ REMOVED",
}


def footer(n) -> str:
    badge = _BADGES.get(get_role(n), "👤 USER")
    return "\n".join(
        [
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"{badge} • +{n}",
            "Developer: 𝙳𝙴𝚅 𝙰𝚄𝚂𝚃𝙸𝙽",
            "SHΔDOWBORNE LΔB 𝚇𝙲𝙾𝚁𝙴®",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ]
    )


def menu(n) -> str:
    role, mode = get_role(n), get_mode()
    premium = role in ("OWNER", "SUDO", "PREMIUM")
    owner = role == "OWNER"
    sudo = role in ("OWNER", "SUDO")
    L = [
        *BANNER, "", "💨 𝚇𝙲𝙾𝚁𝙴® 𝗦𝗬𝗦𝗧𝗘𝗠 𝗖𝗢𝗡𝗦𝗢𝗟𝗘", "",
        f"┃ ✦ 𝗠𝗢𝗗𝗘: {mode.upper()}", f"┃ ✦ ACCESS: {role}", "┃ ✦ PREFIX: ®  |  @", "",
        "╭━━━━━━〔 𝗔𝗖𝗖𝗢𝗨𝗡𝗧 / 𝗗𝗘𝗩𝗜𝗖𝗘 〕",
        "┃ ✞ ®bancheck <number>", "┃ ✞ ®device [number]", "┃ ✞ ®clientid [number]",
        "┃ ✞ ®clientcheck [number]", "┃ ✞ ®get-id", "┃ ✞ ®identify (reply/number)", "┃ ✞ ®session", "╰━━━━━━━━━━━━━━━━━━", "",
        "╭━━━━━━〔 𝗦𝗘𝗖𝗨𝗥𝗜𝗧𝗬 〕", "┃ ✞ ®antibug (log)  ®antibug status", "┃ ✞ ®antibug on|off  block|purge on|off  level low|normal|high",
        "┃ ✞ ®antibug trust|untrust|unblock <number>  list",
        "┃ ✞ Bug detection scored 0-100 → auto-block, purge, shield, effectiveness %", "╰━━━━━━━━━━━━━━━━━━", "",
        "╭━━━━━━〔 𝗚𝗥𝗢𝗨𝗣 𝗖𝗢𝗡𝗧𝗥𝗢𝗟 〕", "┃ ✞ ®ban", "┃ ✞ ®ordain / ®strip",
        "┃ ✞ ®expel (reply to member)", "┃ ✞ ®jid", "┃ ✞ ®sweepclean (owner; confirmation)",
        "┃ ✞ ®purge (owner)", "╰━━━━━━━━━━━━━━━━━━", "",
        "╭━━━━━━〔 𝗨𝗧𝗜𝗟𝗜𝗧𝗜𝗘𝗦 〕", "┃ ✞ ®save contact <tag> <number>",
        "┃ ✞ ®save contact <tag> (reply)", "┃ ✞ ®readqr (reply to QR image)", "╰━━━━━━━━━━━━━━━━━━", "",
    ]
    if sudo:
        L += ["╭━━━━━━〔 𝗗𝗘𝗩𝗘𝗟𝗢𝗣𝗘𝗥 𝗧𝗢𝗢𝗟𝗦 〕", "┃ ✞ ®gitclone <HTTPS repository URL>",
              "┃ ✞ ®mediafire <public download link>", "┃ ✞ ®apify help / actors / me",
              "┃ ✞ ®apify run <alias> <JSON>", "┃ ✞ ®apilab get <url>", "┃ ✞ ®watch <url>",
              "┃ ✞ ®monitor <url> / list / remove", "╰━━━━━━━━━━━━━━━━━━", ""]
    if premium:
        L += ["╭━━━━━━〔 𝗢𝗦𝗜𝗡𝗧 〕", "┃ ✞ ®groups <keyword> [country] [max]",
              "┃ ✞ ®wafind <keyword>", "┃ ✞ ®getwadetail <number>", "┃ ✞ ®getgcdetail <jid>",
              "┃ ✞ ®agecheck <number>", "┃ ✞ ®tgfind <keyword>", "┃ ✞ ®tgdetail <@user>",
              "┃ ✞ ®tggcdetail <@channel>", "┃ ✞ ®tgage <id>", "╰━━━━━━━━━━━━━━━━━━", ""]
    else:
        L += ["╭━━━━━━〔 𝗢𝗦𝗜𝗡𝗧 · 𝗣𝗥𝗘𝗠𝗜𝗨𝗠 〕", "┃ ✞ 🔒 ®buypremium (Buy premium)", "╰━━━━━━━━━━━━━━━━━━", ""]
    L += ["╭━━━━━━〔 𝗦𝗬𝗦𝗧𝗘𝗠 〕", "┃ ✞ ®ping", "┃ ✞ ®botinfo", "┃ ✞ ®health",
          "┃ ✞ ®services", "┃ ✞ ®stats", "┃ ✞ ®uptime", "┃ ✞ ®checkaccess", "╰━━━━━━━━━━━━━━━━━━", ""]
    if owner:
        L += ["╭━━━━━━〔 𝗢𝗪𝗡𝗘𝗥 𝗖𝗢𝗡𝗧𝗥𝗢𝗟 〕", "┃ ✞ ®sudo / ®delsudo (reply)",
              "┃ ✞ ®premium add|remove|list", "┃ ✞ ®localban / ®unban", "┃ ✞ ®remove / ®restore",
              "┃ ✞ ®banned", "┃ ✞ ®restart", "┃ ✞ ®scan (reply)", "┃ ✞ ®restore <number>", "╰━━━━━━━━━━━━━━━━━━", ""]
    L += ["JUST Δ LITTLE UNIMΔGINΔΔRY", footer(n)]
    return "\n".join(L)


def restricted_card(reason: str = "This command is not available to your account.") -> str:
    return "\n".join([
        "╭━━━〔 𝚇𝙲𝙾𝚁𝙴® 〕━━━╮", "", "RESTRICTED ACCESS ✘",
        "╭────────────────────╮", "┃  ACCESS DENIED", f"┃  {reason[:180]}",
        "┃  Contact the bot owner for access.", "╰────────────────────╯", "",
        "𝙳𝙴𝚅 𝙰𝚄𝚂𝚃𝙸𝙽 · SHΔDOWBORNE LΔB",
    ])


_REGIONS = {
    "234": "NG (+234)",
    "233": "GH (+233)",
    "254": "KE (+254)",
    "27": "ZA (+27)",
    "44": "UK (+44)",
    "1": "US (+1)",
}


def region(p) -> str:
    p = re.sub(r"\D", "", str(p))
    for length in (3, 2, 1):  # longest dial-code prefix wins
        if p[:length] in _REGIONS and len(p[:length]) == length:
            return _REGIONS[p[:length]]
    return "Unknown"


def ban_card(p, r: dict, n) -> str:
    st = {True: "🔴 BANNED", False: "🟢 NOT BANNED"}.get(r.get("banned"), "⚠️ UNKNOWN")
    return "\n".join(
        [
            "*𝚇𝙲𝙾𝚁𝙴® WHATSAPP BAN CHECK*",
            "",
            f"Target: {p}",
            f"Region: {region(p)}",
            f"Status: {st}",
            f"Reason: {r.get('reason') or 'No reason provided'}",
            footer(n),
        ]
    )


def device_card(i: dict, n) -> str:
    return "\n".join(
        [
            *BANNER,
            "",
            "✦ 𝗗𝗘𝗩𝗜𝗖𝗘 𝗜𝗡𝗙𝗢",
            "",
            f"Name: {i.get('name') or 'ANDROID'}",
            f"Platform: {i.get('platform') or 'Unknown'}",
            f"Version: {i.get('version') or 'UNAVAILABLE'}",
            f"OS Build: {i.get('os') or 'iOS'}",
            footer(n),
        ]
    )


def access_card(n) -> str:
    role = get_role(n)
    c = clean(n)
    L = [*BANNER, "", "✦ 𝗔𝗖𝗖𝗘𝗦𝗦 𝗦𝗧𝗔𝗧𝗨𝗦", "", f"Number: +{n}", f"Role: {role}"]
    if role == "BANNED":
        b = next((x for x in list_banned() if x["number"] == c), None) or {}
        left = b.get("daysLeft")
        L.append(f"Reason: {b.get('reason') or 'N/A'}")
        L.append(f"Until: {'Permanent' if left == 'permanent' else f'{left} days'}")
    if role == "PREMIUM":
        p = next((x for x in list_premium() if x["number"] == c), None)
        if p:
            L.append(f"Plan: {p['plan']} ({p['daysLeft']} days left)")
    L.append(footer(n))
    return "\n".join(L)
