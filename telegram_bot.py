"""Telegram dashboard: force-join gate, WhatsApp pairing, and owner status commands."""
import asyncio
import re
from typing import Callable
from urllib.parse import urlparse

from . import config, db, runtime

PAIR_CONFIRM_TIMEOUT = 180
XCORE_HEADER = "╭━━━〔 𝚇𝙲𝙾𝚁𝙴® 〕━━━╮"
XCORE_FOOTER = "╰━━━━━━━━━━━━━━━━━━╯\nDeveloper: 𝙳𝙴𝚅 𝙰𝚄𝚂𝚃𝙸𝙽\nSHΔDOWBORNE LΔB 𝚇𝙲𝙾𝚁𝙴®"


def _brand_text(value: str) -> str:
    text = str(value or "").strip()
    if not text.startswith(XCORE_HEADER):
        text = f"{XCORE_HEADER}\n\n{text}" if text else XCORE_HEADER
    if "Developer: 𝙳𝙴𝚅 𝙰𝚄𝚂𝚃𝙸𝙽" not in text:
        text = f"{text.rstrip()}\n\n{XCORE_FOOTER}"
    # Some Telegram texts already carry the developer footer; keep it exactly once.
    return text


def _owner_telegram_ids() -> tuple[int, ...]:
    cfg = config.get().telegram
    return cfg.owner_ids or (cfg.admin_id,)


def _is_admin(update) -> bool:
    u = update.effective_user
    return bool(u) and u.id in _owner_telegram_ids()


def _target_from_url(url: str) -> str | None:
    """Return a public Telegram username or numeric chat ID that get_chat_member can check."""
    raw = (url or "").strip()
    if raw.startswith("@"):
        return raw
    if re.fullmatch(r"-?\d+", raw):
        return raw
    parsed = urlparse(raw if "://" in raw else "https://" + raw)
    if parsed.netloc.lower() not in {"t.me", "www.t.me", "telegram.me", "www.telegram.me"}:
        return None
    path = parsed.path.strip("/").split("/")
    if not path or not path[0] or path[0].startswith(("+", "joinchat")):
        return None
    if path[0] == "c" or path[0].startswith("addstickers"):
        return None
    return "@" + path[0] if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", path[0]) else None


class TelegramBot:
    def __init__(self, list_sessions: Callable[[], list], create_session: Callable | None = None):
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
        from telegram.ext import (
            Application, CallbackQueryHandler, CommandHandler,
            MessageHandler, TypeHandler, PreCheckoutQueryHandler, filters,
        )

        cfg = config.get()
        self._list_sessions = list_sessions
        self._create_session = create_session
        self._InlineKeyboardButton = InlineKeyboardButton
        self._InlineKeyboardMarkup = InlineKeyboardMarkup
        self._ReplyKeyboardMarkup = ReplyKeyboardMarkup
        self._ApplicationHandlerStop = __import__("telegram.ext", fromlist=["ApplicationHandlerStop"]).ApplicationHandlerStop
        self.app = Application.builder().token(cfg.telegram.token).build()

        # The gate runs before commands. Users must satisfy every configured public channel/group.
        self.app.add_handler(TypeHandler(Update, self._force_join_gate), group=-1)
        self.app.add_handler(CallbackQueryHandler(self._check_join, pattern=r"^check_join$"))
        self.app.add_handler(CallbackQueryHandler(self._buy_callback, pattern=r"^buy:(30|365)$"))
        self.app.add_handler(CallbackQueryHandler(self._admin_callback, pattern=r"^xadmin:"))
        self.app.add_handler(CommandHandler("start", self._start))
        self.app.add_handler(CommandHandler("pair", self._pair))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._pair_phone))
        self.app.add_handler(CommandHandler("status", self._status))
        self.app.add_handler(CommandHandler("stats", self._stats))
        self.app.add_handler(CommandHandler("sessions", self._sessions))
        self.app.add_handler(CommandHandler("admin", self._admin))
        self.app.add_handler(CommandHandler("pairnumber", self._pairnumber))
        self.app.add_handler(CommandHandler("unpair", self._unpair))
        self.app.add_handler(CommandHandler("paired", self._paired))
        self.app.add_handler(CommandHandler("ram", self._ram))
        self.app.add_handler(CommandHandler("grantpremium", self._grantpremium))
        self.app.add_handler(CommandHandler("mode", self._mode))
        self.app.add_handler(CommandHandler("antiban", self._antiban))
        self.app.add_handler(CommandHandler("buy", self._buy))
        self.app.add_handler(CommandHandler("revokepremium", self._revokepremium))
        self.app.add_handler(CommandHandler("sudoadd", self._sudoadd))
        self.app.add_handler(CommandHandler("sudoremove", self._sudoremove))
        self.app.add_handler(PreCheckoutQueryHandler(self._precheckout))
        self.app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, self._successful_payment))
        self.running = False

    async def _reply_text(self, message, text, **kwargs):
        return await message.reply_text(_brand_text(text), **kwargs)

    async def _edit_message_text(self, target, text, **kwargs):
        return await target.edit_message_text(_brand_text(text), **kwargs)

    async def _joined_all(self, user_id: int, context) -> tuple[bool, list[str]]:
        urls = config.get().telegram.force_join_urls
        missing = []
        for url in urls:
            target = _target_from_url(url)
            # Invite links do not expose a chat ID to Telegram's membership API; fail closed.
            if target is None:
                missing.append(url)
                continue
            try:
                member = await context.bot.get_chat_member(chat_id=target, user_id=user_id)
                if str(member.status).lower() not in {"member", "administrator", "creator", "owner"}:
                    missing.append(url)
            except Exception:  # noqa: BLE001 — inability to verify must not bypass the gate
                missing.append(url)
        return not missing, missing

    def _join_markup(self):
        urls = config.get().telegram.force_join_urls
        rows = [[self._InlineKeyboardButton(f"Join channel/group {i}", url=url)] for i, url in enumerate(urls, 1)]
        rows.append([self._InlineKeyboardButton("✅ I've Joined — Check Again", callback_data="check_join")])
        return self._InlineKeyboardMarkup(rows)

    async def _show_join_prompt(self, update, context, edit=False):
        urls = config.get().telegram.force_join_urls
        text = "🔒 To use 𝚇𝙲𝙾𝚁𝙴®, join all the Telegram channels/groups below, then tap ‘I've Joined — Check Again’."
        if any(_target_from_url(url) is None for url in urls):
            text += "\n\n⚠️ One configured URL cannot be verified automatically. Use a public t.me/<username> link or a numeric chat ID in the environment settings."
        if edit and update.callback_query:
            await self._edit_message_text(update.callback_query, text, reply_markup=self._join_markup())
        elif update.effective_message:
            await self._reply_text(update.effective_message, text, reply_markup=self._join_markup())

    async def _force_join_gate(self, update, context):
        if not config.get().telegram.force_join_urls:
            return
        user = update.effective_user
        if not user or user.id in _owner_telegram_ids():
            return
        query = update.callback_query
        if query and query.data == "check_join":
            return
        # Avoid intercepting callback buttons unrelated to force-join; all other bot use is gated.
        ok, _ = await self._joined_all(user.id, context)
        if ok:
            return
        await self._show_join_prompt(update, context)
        raise self._ApplicationHandlerStop

    async def _check_join(self, update, context):
        query = update.callback_query
        await query.answer()
        user = update.effective_user
        if not user:
            return
        ok, _ = await self._joined_all(user.id, context)
        if ok:
            await self._edit_message_text(query, "✅ Membership verified. You can now use 𝚇𝙲𝙾𝚁𝙴®. Send /start to open the dashboard.")
        else:
            await self._show_join_prompt(update, context, edit=True)

    async def _guard(self, update) -> bool:
        if _is_admin(update):
            return True
        message = update.effective_message
        if message:
            await self._reply_text(message, "⛔ Owner dashboard command. Use /pair to connect your own WhatsApp account.")
        return False

    async def _start(self, update, _ctx):
        urls = config.get().telegram.force_join_urls
        user = update.effective_user
        if urls and user and user.id not in _owner_telegram_ids():
            ok, _ = await self._joined_all(user.id, _ctx)
            if not ok:
                await self._show_join_prompt(update, _ctx)
                return
        if user and user.id in _owner_telegram_ids():
            return await self._admin(update, _ctx)
        message = update.effective_message
        # Telegram gets the brand image only; the XCORE soundtrack is WhatsApp-only.
        asset = __import__("pathlib").Path(__file__).resolve().parent / "assets" / "xcore-brand.png"
        if message and asset.is_file():
            with asset.open("rb") as photo:
                await message.reply_photo(photo=photo, caption="XCORE® | WhatsApp Dev Bot\nAUTOMATE • CONTROL • DOMINATE")
        keyboard = self._ReplyKeyboardMarkup([["/pair", "/status"], ["/buy"]], resize_keyboard=True, one_time_keyboard=False)
        await self._reply_text(message,
            f"🩸 {config.get().bot.name} dashboard\n\n/pair — connect your WhatsApp account\n/status — connection status\n/buy — Premium plans",
            reply_markup=keyboard,
        )

    async def _pair(self, update, context):
        user = update.effective_user
        if not user or not update.effective_message:
            return
        if context.user_data.get("pairing_in_progress"):
            await self._reply_text(update.effective_message, "🌀 A pairing attempt is already in progress. Please wait for its result.")
            return
        if not self._create_session:
            await self._reply_text(update.effective_message, "❌ WhatsApp pairing is unavailable in this deployment.")
            return
        context.user_data["awaiting_pair_phone"] = True
        await self._reply_text(update.effective_message, 
            "📱 Enter the WhatsApp phone number with country code.\nExample: +2348012345678\n\nSend /pair again to restart this step."
        )

    async def _pair_phone(self, update, context):
        if not context.user_data.get("awaiting_pair_phone"):
            return
        message = update.effective_message
        user = update.effective_user
        if not message or not user:
            return
        raw = (message.text or "").strip()
        number = re.sub(r"\D", "", raw)
        if not raw.startswith("+") or not 7 <= len(number) <= 15:
            await self._reply_text(message, "❌ Invalid phone number. Include the country code, for example +2348012345678.")
            return
        context.user_data.pop("awaiting_pair_phone", None)
        context.user_data["pairing_in_progress"] = True
        await self._reply_text(message, "🌀 Requesting pairing code… Please wait.")
        try:
            result = await self._create_session(number, request_pairing=True)
        except Exception as exc:  # noqa: BLE001
            result = {"error": str(exc), "pairingCode": None, "session": None}
        session = result.get("session")
        code = result.get("pairingCode")
        if result.get("error") or (not code and not (session and session.connected.is_set())):
            context.user_data.pop("pairing_in_progress", None)
            await self._pair_failure(message, user.id)
            return
        if code:
            await self._reply_text(message, 
                "🔑 𝚇𝙲𝙾𝚁𝙴® PAIRING CODE\n"
                f"\n`𝚇𝙲𝙾𝚁𝙴® • {code}`\n"
                "Enter only the actual WhatsApp code after `𝚇𝙲𝙾𝚁𝙴® •` (the 𝚇𝙲𝙾𝚁𝙴® label is branding).\n\n"
                "How to pair:\n"
                "1. Open WhatsApp on the phone for this number.\n"
                "2. Open Settings (or ⋮ on Android) → Linked devices.\n"
                "3. Tap Link a device, then Link with phone number instead.\n"
                "4. Enter the actual code shown above in WhatsApp.\n\n"
                "Keep this code private. Waiting for WhatsApp to connect…",
                parse_mode="Markdown",
            )
        if session and session.connected.is_set():
            context.user_data.pop("pairing_in_progress", None)
            db.link_telegram_user(user.id, number)
            await self._pair_success(message, user.id)
            return
        if session:
            try:
                connected = await asyncio.to_thread(session.connected.wait, PAIR_CONFIRM_TIMEOUT)
            except Exception:
                connected = False
            if connected:
                context.user_data.pop("pairing_in_progress", None)
                db.link_telegram_user(user.id, number)
                await self._pair_success(message, user.id)
                return
        context.user_data.pop("pairing_in_progress", None)
        await self._pair_failure(message, user.id)

    async def _pair_success(self, message, telegram_user_id: int):
        if telegram_user_id in _owner_telegram_ids():
            text = "My Lord 𝙳𝙴𝚅 𝙰𝚄𝚂𝚃𝙸𝙽 💀 Welcome to Your Project"
        else:
            text = "You're now connected to SHΔDOWBORNE ⸸ by 𝙳𝙴𝚅 𝙰𝚄𝚂𝚃𝙸𝙽\nStatus = connected 🔗 successfully"
        await self._reply_text(message, text)

    async def _pair_failure(self, message, telegram_user_id: int):
        if telegram_user_id in _owner_telegram_ids():
            text = "My Lord Something Occurred... Try again or check your bot 🌚"
        else:
            text = "You're a weak mf... try again"
        await self._reply_text(message, text)

    async def _status(self, update, _ctx):
        if await self._guard(update):
            await self._reply_text(update.effective_message, 
                f"🟢 ONLINE\nMode: {db.get_mode()}\nUptime: {runtime.uptime_seconds()}s"
            )

    async def _stats(self, update, _ctx):
        if await self._guard(update):
            s = db.get_stats()
            await self._reply_text(update.effective_message, f"📊 Checks: {s['checks']}\n🔴 Banned: {s['banned']}\n🟢 Safe: {s['safe']}")

    async def _sessions(self, update, _ctx):
        if await self._guard(update):
            text = "\n".join(f"+{x}" for x in self._list_sessions()) or "No active sessions."
            await self._reply_text(update.effective_message, text)

    async def _mode(self, update, _ctx):
        if not await self._guard(update):
            return
        args = (update.effective_message.text or "").split()
        if len(args) == 2:
            try:
                db.set_mode(args[1].lower())
            except ValueError:
                return await self._reply_text(update.effective_message, "Valid modes: public, self, dev, stealth, maintenance")
        await self._reply_text(update.effective_message, f"Current mode: {db.get_mode()}\nUsage to change: /mode public|self|dev|stealth|maintenance")

    def _admin_keyboard(self):
        rows = [
            [self._InlineKeyboardButton("🔗 Pair WhatsApp", callback_data="xadmin:pair"),
             self._InlineKeyboardButton("📋 Paired accounts", callback_data="xadmin:paired")],
            [self._InlineKeyboardButton("🧠 RAM & system", callback_data="xadmin:ram"),
             self._InlineKeyboardButton("📊 Overview", callback_data="xadmin:overview")],
            [self._InlineKeyboardButton("⚙️ Admin controls", callback_data="xadmin:controls")],
        ]
        return self._InlineKeyboardMarkup(rows)

    async def _admin(self, update, _ctx):
        if not await self._guard(update):
            return
        active = len(self._list_sessions())
        paired = len(db.list_paired())
        text = ("🛡️ OWNER CONTROL PANEL\n\n"
                f"Active WhatsApp sessions: {active}\nRegistered paired numbers: {paired}\n"
                f"Mode: {db.get_mode()}\nUptime: {runtime.uptime_seconds()} seconds\n\n"
                "Select a dashboard section below.\n"
                "Commands: /pairnumber <number> · /unpair <number> · /grantpremium <telegram_id> <whatsapp_number> <days>")
        await self._reply_text(update.effective_message, text, reply_markup=self._admin_keyboard())

    async def _admin_callback(self, update, context):
        query = update.callback_query
        await query.answer()
        if not _is_admin(update):
            return await self._edit_message_text(query, "⛔ Owner-only dashboard.")
        action = str(query.data).split(":", 1)[1]
        if action == "overview":
            text = ("📊 XCORE OVERVIEW\n"
                    f"Active sessions: {len(self._list_sessions())}\n"
                    f"Paired accounts: {len(db.list_paired())}\n"
                    f"SUDO accounts: {len(db.list_sudo())}\n"
                    f"Premium accounts: {len(db.list_premium())}\n"
                    f"Mode: {db.get_mode()}\nUptime: {runtime.uptime_seconds()}s")
        elif action == "paired":
            active = set(self._list_sessions())
            links = {x["number"]: x["telegram_id"] for x in db.list_telegram_links()}
            numbers = db.list_paired()
            lines = [f"{'🟢' if n in active else '⚪'} +{n} | Telegram: {links.get(n, 'not linked')}" for n in numbers]
            text = "📋 PAIRED WHATSAPP ACCOUNTS\n\n" + ("\n".join(lines) if lines else "No paired accounts recorded.")
            rows = [[self._InlineKeyboardButton(f"Unpair +{n}", callback_data=f"xadmin:unpair:{n}")] for n in numbers[:15]]
            rows.append([self._InlineKeyboardButton("↩ Back to dashboard", callback_data="xadmin:overview")])
            return await self._edit_message_text(query, text, reply_markup=self._InlineKeyboardMarkup(rows))
        elif action.startswith("unpair:"):
            number = db.clean(action.split(":", 1)[1])
            if not 7 <= len(number) <= 15:
                text = "Invalid WhatsApp number."
            else:
                rows = [[self._InlineKeyboardButton("⚠️ Confirm unpair", callback_data=f"xadmin:unpairconfirm:{number}")],
                        [self._InlineKeyboardButton("Cancel", callback_data="xadmin:paired")]]
                return await self._edit_message_text(query,
                    f"Confirm removal of XCORE's local WhatsApp session for +{number}?\n\nThis deletes the bot's local linked-device credentials. It does not revoke other linked devices in WhatsApp settings.",
                    reply_markup=self._InlineKeyboardMarkup(rows))
        elif action.startswith("unpairconfirm:"):
            number = db.clean(action.split(":", 1)[1])
            from .whatsapp import socket as wa
            ok = wa.disconnect_session(number)
            text = (f"✅ Local WhatsApp session for +{number} was unpaired." if ok
                    else f"No local session or saved credentials were found for +{number}.")
        elif action == "ram":
            text = self._memory_report()
        elif action == "controls":
            text = ("⚙️ ADMIN CONTROLS\n\n"
                    "/pairnumber <number> — pair another WhatsApp account\n"
                    "/unpair <number> — disconnect a paired account\n"
                    "/grantpremium <telegram_id> <whatsapp_number> <days>\n"
                    "/revokepremium <telegram_id>\n"
                    "/sudoadd <whatsapp_number>\n/sudoremove <whatsapp_number>\n"
                    "/mode <mode> — change bot mode\n"
                    "/antiban on|off — toggle protection\n"
                    "Premium grants: /grantpremium <telegram_id> <whatsapp_number> <days>")
        elif action == "pair":
            text = "🔗 To pair an account, send:\n/pairnumber +2348012345678\n\nThe bot will return a WhatsApp linking code. Keep it private."
        else:
            text = "Unknown dashboard section."
        await self._edit_message_text(query, text, reply_markup=self._admin_keyboard())

    @staticmethod
    def _memory_report() -> str:
        rss = None
        try:
            import psutil
            process = psutil.Process()
            rss = process.memory_info().rss
            total = psutil.virtual_memory().total
            percent = process.memory_percent()
            return ("🧠 SYSTEM RESOURCE USAGE\n\n"
                    f"Process RAM: {rss / 1024 / 1024:.1f} MB\n"
                    f"Host RAM: {total / 1024 / 1024:.1f} MB\n"
                    f"Process RAM share: {percent:.2f}%")
        except Exception:
            try:
                with open("/proc/self/status", "r", encoding="utf-8") as f:
                    for line in f:
                        if line.startswith("VmRSS:"):
                            return "🧠 PROCESS RAM\n\n" + f"{int(line.split()[1]) / 1024:.1f} MB (resident set)"
            except Exception:
                pass
        return "RAM metrics are unavailable on this host."

    async def _pairnumber(self, update, _ctx):
        if not await self._guard(update):
            return
        message = update.effective_message
        args = (message.text or "").split() if message else []
        number = db.clean(args[1]) if len(args) == 2 else ""
        if not 7 <= len(number) <= 15:
            return await self._reply_text(message, "Usage: /pairnumber <international_number>\nExample: /pairnumber +2348012345678")
        await self._reply_text(message, f"🔄 Starting WhatsApp pairing for +{number}…")
        try:
            result = await self._create_session(number, request_pairing=True) if self._create_session else {"error": "Pairing unavailable"}
        except Exception as exc:
            result = {"error": str(exc)}
        session = result.get("session")
        code = result.get("pairingCode")
        if result.get("error"):
            return await self._reply_text(message, f"❌ Pairing failed: {str(result['error'])[:300]}")
        if code:
            await self._reply_text(message, f"🔑 WHATSAPP LINKING CODE\n\n{code}\n\nEnter this in WhatsApp → Linked devices → Link with phone number. Keep it private.")
        if session and session.connected.is_set():
            db.add_paired(number)
            await self._reply_text(message, f"✅ +{number} is connected and registered as a paired account.")
        elif session:
            await self._reply_text(message, "⏳ Pairing request started. Once WhatsApp confirms the link, the account will appear in /paired. If it does not connect, retry after checking the code.")

    async def _unpair(self, update, _ctx):
        if not await self._guard(update):
            return
        args = (update.effective_message.text or "").split()
        number = db.clean(args[1]) if len(args) == 2 else ""
        if not 7 <= len(number) <= 15:
            return await self._reply_text(update.effective_message, "Usage: /unpair <whatsapp_number>")
        from .whatsapp import socket as wa
        disconnected = wa.disconnect_session(number)
        await self._reply_text(update.effective_message,
            f"{'🔌 Unpaired' if disconnected else 'ℹ️ No local session found for'} +{number}.\nThe bot's local linked-device credentials were removed. This does not revoke other linked devices from the WhatsApp mobile app; check WhatsApp → Linked devices if you also want to revoke them there.")

    async def _paired(self, update, _ctx):
        if not await self._guard(update):
            return
        active = set(self._list_sessions())
        links = {x["number"]: x["telegram_id"] for x in db.list_telegram_links()}
        lines = [f"{'🟢' if n in active else '⚪'} +{n} | Telegram ID: {links.get(n, 'not linked')}" for n in db.list_paired()]
        await self._reply_text(update.effective_message, "📋 PAIRED ACCOUNTS\n\n" + ("\n".join(lines) if lines else "No paired accounts."))

    async def _ram(self, update, _ctx):
        if await self._guard(update):
            await self._reply_text(update.effective_message, self._memory_report())

    async def _antiban(self, update, _ctx):
        if not await self._guard(update):
            return
        args = (update.effective_message.text or "").split()
        if len(args) == 2 and args[1].lower() in {"on", "off"}:
            db.set_antiban(args[1].lower() == "on")
        await self._reply_text(update.effective_message,
            f"Antiban protection: {'ON' if db.get_antiban() else 'OFF'}\nUsage: /antiban on|off")

    async def _buy(self, update, context):
        message = update.effective_message
        user = update.effective_user
        if not message or not user:
            return
        cfg = config.get().telegram
        options = []
        if cfg.premium_30d_stars > 0:
            options.append(("30 days", 30, cfg.premium_30d_stars))
        if cfg.premium_365d_stars > 0:
            options.append(("365 days", 365, cfg.premium_365d_stars))
        if not options:
            return await self._reply_text(message, "💎 Premium purchases are not configured yet. Contact the owner.")
        keyboard = [[self._InlineKeyboardButton(f"{label} — ⭐ {price}", callback_data=f"buy:{days}")]
                    for label, days, price in options]
        # Callback handler is registered below in __init__.
        await self._reply_text(message, "💎 Choose a XCORE Premium plan:",
                               reply_markup=self._InlineKeyboardMarkup(keyboard))

    async def _buy_callback(self, update, context):
        query = update.callback_query
        await query.answer()
        user = update.effective_user
        if not user or not query.data.startswith("buy:"):
            return
        try:
            days = int(query.data.split(":", 1)[1])
        except ValueError:
            return await self._edit_message_text(query, "Invalid plan selection.")
        cfg = config.get().telegram
        price = cfg.premium_30d_stars if days == 30 else cfg.premium_365d_stars if days == 365 else 0
        if price <= 0:
            return await self._edit_message_text(query, "This plan is currently unavailable.")
        from telegram import LabeledPrice
        await context.bot.send_invoice(
            chat_id=query.message.chat_id,
            title=f"XCORE Premium — {days} days",
            description=f"{days} days of 𝚇𝙲𝙾𝚁𝙴® Premium access. Developer: 𝙳𝙴𝚅 𝙰𝚄𝚂𝚃𝙸𝙽",
            payload=f"xcore-premium:{user.id}:{days}",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(f"Premium for {days} days", price)],
        )

    async def _precheckout(self, update, context):
        query = update.pre_checkout_query
        try:
            parts = str(query.invoice_payload).split(":")
            valid = len(parts) == 3 and parts[0] == "xcore-premium" and int(parts[1]) == query.from_user.id
            days = int(parts[2]) if valid else 0
            cfg = config.get().telegram
            expected = cfg.premium_30d_stars if days == 30 else cfg.premium_365d_stars if days == 365 else 0
            valid = valid and expected > 0 and query.currency == "XTR" and query.total_amount == expected
        except Exception:
            valid = False
        if valid:
            await query.answer(ok=True)
        else:
            await query.answer(ok=False, error_message="This XCORE Premium invoice is invalid or expired.")

    async def _successful_payment(self, update, context):
        message = update.effective_message
        user = update.effective_user
        payment = getattr(message, "successful_payment", None) if message else None
        if not user or not payment:
            return
        try:
            payload = str(payment.invoice_payload).split(":")
            if len(payload) != 3 or payload[0] != "xcore-premium" or int(payload[1]) != user.id:
                raise ValueError("Invalid payment payload")
            days = int(payload[2])
            cfg = config.get().telegram
            expected = cfg.premium_30d_stars if days == 30 else cfg.premium_365d_stars if days == 365 else 0
            if payment.currency != "XTR" or payment.total_amount != expected or expected <= 0:
                raise ValueError("Payment amount mismatch")
            applied, phone = db.apply_telegram_payment(payment.telegram_payment_charge_id, user.id, days)
            if not applied:
                return await self._reply_text(message, "ℹ️ This Telegram Stars payment was already processed.")
            status = f"Premium activated for linked WhatsApp number +{phone}." if phone else "Payment received. Premium is reserved for your Telegram account; pair your WhatsApp number with /pair to activate it."
            await self._reply_text(message, f"✅ Payment successful.\n{status}\nPlan: {days} days.")
        except Exception as exc:
            await self._reply_text(message, f"⚠️ Payment was received but could not be applied automatically. Contact the owner with your Telegram payment receipt.\nReference: {type(exc).__name__}")

    async def _grantpremium(self, update, _ctx):
        if not await self._guard(update):
            return
        args = (update.effective_message.text or "").split()
        if len(args) == 4 and args[1].isdigit() and args[3].isdigit():
            try:
                db.grant_premium_identity(int(args[1]), args[2], int(args[3]), by=str(update.effective_user.id))
            except ValueError as exc:
                return await self._reply_text(update.effective_message, f"❌ {exc}")
            return await self._reply_text(update.effective_message,
                f"💎 Premium granted.\nTelegram ID: {args[1]}\nWhatsApp: +{db.clean(args[2])}\nDuration: {args[3]} days.")
        if len(args) == 3 and args[1].isdigit() and args[2].isdigit():
            phone = db.grant_telegram_premium(int(args[1]), int(args[2]), "owner-grant")
            return await self._reply_text(update.effective_message,
                "💎 Premium granted to linked number +" + phone if phone else "💎 Premium reserved until the user pairs WhatsApp.")
        await self._reply_text(update.effective_message,
            "Usage: /grantpremium <telegram_user_id> <whatsapp_number> <days>\nOr: /grantpremium <telegram_user_id> <days> for an already-linked user.")

    async def _revokepremium(self, update, _ctx):
        if not await self._guard(update):
            return
        args = (update.effective_message.text or "").split()
        if len(args) != 2 or not args[1].isdigit():
            return await self._reply_text(update.effective_message, "Usage: /revokepremium <telegram_user_id>")
        db.revoke_telegram_premium(int(args[1]))
        await self._reply_text(update.effective_message, "💎 Telegram-linked Premium access revoked.")

    async def _sudoadd(self, update, _ctx):
        if not await self._guard(update):
            return
        args = (update.effective_message.text or "").split()
        number = db.clean(args[1]) if len(args) == 2 else ""
        if not 7 <= len(number) <= 15:
            return await self._reply_text(update.effective_message, "Usage: /sudoadd <whatsapp_number>")
        db.add_sudo(number)
        await self._reply_text(update.effective_message, f"⚔️ SUDO access granted to +{number}.")

    async def _sudoremove(self, update, _ctx):
        if not await self._guard(update):
            return
        args = (update.effective_message.text or "").split()
        number = db.clean(args[1]) if len(args) == 2 else ""
        if not number:
            return await self._reply_text(update.effective_message, "Usage: /sudoremove <whatsapp_number>")
        db.remove_sudo(number)
        await self._reply_text(update.effective_message, f"⚔️ SUDO access removed from +{number}.")

    async def launch(self, timeout: float = 15.0) -> None:
        await asyncio.wait_for(self.app.initialize(), timeout)
        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=True)
        try:
            from telegram import BotCommand
            await self.app.bot.set_my_commands([
                BotCommand("start", "Open 𝚇𝙲𝙾𝚁𝙴® dashboard"),
                BotCommand("pair", "Pair a WhatsApp number"),
                BotCommand("status", "Application status"),
                BotCommand("stats", "Usage statistics"),
                BotCommand("sessions", "Connected WhatsApp sessions"),
                BotCommand("paired", "Owner: list all paired accounts"),
                BotCommand("unpair", "Owner: unpair a WhatsApp account"),
                BotCommand("admin", "Open owner control dashboard"),
                BotCommand("pairnumber", "Owner: pair a WhatsApp number"),
                BotCommand("unpair", "Owner: disconnect a WhatsApp number"),
                BotCommand("paired", "Owner: list paired WhatsApp accounts"),
                BotCommand("ram", "Owner: process RAM usage"),
                BotCommand("antiban", "Owner: toggle antiban protection"),
                BotCommand("mode", "View or change bot mode"),
                BotCommand("buy", "Buy XCORE Premium with Telegram Stars"),
                BotCommand("grantpremium", "Owner: grant Premium"),
                BotCommand("revokepremium", "Owner: revoke Premium"),
                BotCommand("sudoadd", "Owner: grant WhatsApp SUDO"),
                BotCommand("sudoremove", "Owner: revoke WhatsApp SUDO"),
            ])
        except Exception:  # noqa: BLE001 — command-menu setup must not prevent bot startup
            pass
        self.running = True

    async def send(self, chat_id: int, text: str) -> None:
        await self.app.bot.send_message(chat_id=chat_id, text=_brand_text(text))

    async def stop(self) -> None:
        if not self.running:
            return
        self.running = False
        for step in (self.app.updater.stop, self.app.stop, self.app.shutdown):
            try:
                await step()
            except Exception:  # noqa: BLE001 — best effort during shutdown
                pass
