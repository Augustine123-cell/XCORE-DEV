"""Application entrypoint: health server + Telegram admin bot + WhatsApp session + monitor."""
import asyncio
import os
import signal
import sys

from . import config, db, logger, monitor, paths, server


async def amain() -> None:
    try:
        cfg = config.get()
    except config.ConfigError as e:
        logger.error(f"Fatal config error: {e}")
        logger.error(
            "Set every required variable (TELEGRAM_BOT_TOKEN, BAN_CHECKER_API_KEY) in your host's "
            "Environment Variables panel — a bundled .env file is NOT read on Railway/most hosts."
        )
        sys.exit(1)

    try:
        from .whatsapp import socket as wa
    except ImportError as e:
        logger.error(f"Missing dependency: {e}. Run: pip install -r requirements.txt")
        sys.exit(1)

    loop = asyncio.get_running_loop()
    loop.set_exception_handler(lambda _l, ctx: logger.error("unhandled:", ctx.get("exception") or ctx.get("message")))
    stop = asyncio.Event()

    logger.ok(f"{cfg.bot.name} v1.0 starting…")
    st = paths.bootstrap(cfg.paths.data, cfg.paths.sessions)
    logger.info(f"Data dir: {st['data']}  •  Sessions dir: {st['sessions']}")
    if st["seeded"]:
        logger.info(f"Seeded defaults: {', '.join(st['seeded'])}")
    for w in st["warnings"]:
        logger.warn(w)
    if st["persistent"]:
        logger.ok("Persistent storage OK — data survives redeploys")
    db.load()
    server.start(lambda: len(wa.list_sessions()))

    # Telegram admin bot — optional: a bad token must not block WhatsApp pairing.
    tg = None
    try:
        from .telegram_bot import TelegramBot

        tg = TelegramBot(wa.list_sessions, wa.create_session)
        await tg.launch(timeout=15)
    except Exception as e:  # noqa: BLE001
        logger.error("Telegram bot failed to launch (continuing without it):", repr(e) if not str(e) else e)
        logger.error("Check that TELEGRAM_BOT_TOKEN is valid and not revoked.")
        tg = None

    # WhatsApp
    try:
        if db.is_session_disabled(cfg.owner.number):
            logger.info(f"Owner WhatsApp session +{cfg.owner.number} is disabled from the Telegram dashboard; skipping auto-pair.")
            res = {"session": None, "pairingCode": None, "error": None}
        else:
            res = await wa.create_session(cfg.owner.number, request_pairing=True)
    except ImportError as e:
        logger.error(f"WhatsApp library missing ({e}). Run: pip install -r requirements.txt")
        res = {"error": "dependency missing"}
    except Exception as e:  # noqa: BLE001
        res = {"error": str(e)}
    if res.get("error"):
        logger.error("Pairing failed:", res["error"])
    elif res.get("pairingCode"):
        code = res["pairingCode"]
        logger.ok(f"Pairing code: {code}")
        if tg:
            for owner_id in (cfg.telegram.owner_ids or (cfg.telegram.admin_id,)):
                try:
                    await tg.send(owner_id, f"🔑 𝚇𝙲𝙾𝚁𝙴® • Pairing code for +{cfg.owner.number}\n\n{code}")
                except Exception:  # noqa: BLE001
                    pass

    async def notify(chat_jid: str, text: str) -> None:
        for sess in list(wa.sessions.values()):
            try:
                await sess.send_text(chat_jid, text)
                return
            except Exception:  # noqa: BLE001
                continue

    monitor.set_notifier(notify)
    monitor.start()

    def request_stop(sig_name: str) -> None:
        logger.warn(f"Shutdown ({sig_name})")
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_stop, sig.name)
        except NotImplementedError:  # Windows
            signal.signal(sig, lambda *_a, n=sig.name: loop.call_soon_threadsafe(request_stop, n))

    await stop.wait()

    monitor.stop()
    if tg:
        await tg.stop()
    server.stop()
    wa.disconnect_all()
    await asyncio.sleep(0.3)
    os._exit(0)  # the WhatsApp library's worker threads can otherwise keep the process alive


def run() -> None:
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass
    except Exception as e:  # noqa: BLE001
        logger.error("Fatal:", e)
        sys.exit(1)
