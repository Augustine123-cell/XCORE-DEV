"""Background uptime monitor: polls every registered URL once a minute."""
import asyncio
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

import httpx

from . import db, logger
from .urlguard import assert_safe_url

INTERVAL = 60.0
USER_AGENT = "XCORE/1.0"
BRAND_FOOTER = "\n\n╰━━━━━━━━━━━━━━━━━━╯\nDeveloper: 𝙳𝙴𝚅 𝙰𝚄𝚂𝚃𝙸𝙽\nSHΔDOWBORNE LΔB 𝚇𝙲𝙾𝚁𝙴®"

_task: Optional[asyncio.Task] = None
_notify: Optional[Callable[[str, str], Awaitable[None]]] = None


def set_notifier(fn: Callable[[str, str], Awaitable[None]]) -> None:
    global _notify
    _notify = fn


async def check_one(m: dict) -> dict:
    t = time.monotonic()
    try:
        await assert_safe_url(m["url"])
        async with httpx.AsyncClient(timeout=15, follow_redirects=False, headers={"User-Agent": USER_AGENT}) as http:
            res = await http.get(m["url"])
        return {"up": 200 <= res.status_code < 400, "status": res.status_code, "latency": int((time.monotonic() - t) * 1000)}
    except Exception as e:  # noqa: BLE001 — any failure means "down"
        return {"up": False, "status": 0, "latency": int((time.monotonic() - t) * 1000), "error": str(e)}


async def tick() -> None:
    for m in list(db.list_monitors()):
        r = await check_one(m)
        # snapshot the previous state *before* updating the stored record
        was_up, was_down = m.get("status") == "up", m.get("status") == "down"
        prev_down_since, prev_last_down = m.get("downSince"), m.get("lastDown")
        now = db.now_ms()
        db.update_monitor(
            m["url"],
            {
                "status": "up" if r["up"] else "down",
                "totalChecks": (m.get("totalChecks") or 0) + 1,
                "totalDown": (m.get("totalDown") or 0) + (0 if r["up"] else 1),
                "lastStatus": r["status"],
                "lastLatency": r["latency"],
                "downSince": None if r["up"] else (prev_down_since or now),
                "lastDown": prev_last_down if r["up"] else now,
            },
        )
        if not _notify:
            continue
        if not r["up"] and was_up:
            stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
            await _notify(
                m["chatJid"],
                f"╭━━━〔 𝚇𝙲𝙾𝚁𝙴® 〕━━━╮\n\n🔔 *SERVICE ALERT*\n\nSERVICE: {m['url']}\nSTATUS: DOWN\nLATENCY: —\nTIME: {stamp} UTC{BRAND_FOOTER}",
            )
        elif r["up"] and was_down:
            secs = int((now - prev_down_since) / 1000) if prev_down_since else 0
            await _notify(
                m["chatJid"],
                f"╭━━━〔 𝚇𝙲𝙾𝚁𝙴® 〕━━━╮\n\n🟢 *SERVICE RECOVERED*\n\n{m['url']}\nDowntime: {secs // 60}m {secs % 60}s\nStatus: {r['status']}{BRAND_FOOTER}",
            )


async def _loop() -> None:
    while True:
        try:
            await tick()
        except Exception as e:  # noqa: BLE001
            logger.error("monitor:", e)
        await asyncio.sleep(INTERVAL)


def start() -> None:
    global _task
    if _task and not _task.done():
        return
    logger.info(f"Monitor started (every {int(INTERVAL)}s)")
    _task = asyncio.get_running_loop().create_task(_loop(), name="monitor")


def stop() -> None:
    global _task
    if _task:
        _task.cancel()
        _task = None
