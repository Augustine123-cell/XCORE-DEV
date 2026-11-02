"""WhatsApp transport — the ONLY module that touches the WhatsApp library (neonize / whatsmeow).

neonize runs the WhatsApp connection on its own thread and calls our event callbacks from
there. Every inbound message is normalised into an `Incoming` and handed to the asyncio loop,
so all command code stays plain `async def` Python.
"""
import asyncio
import re
import shutil
import threading
import time
from typing import Any, Optional

from .. import config, db, logger
from .context import Incoming
from .handler import handle_message


def _attr(obj: Any, dotted: str, default: Any = "") -> Any:
    """Safe protobuf/attribute path lookup: _attr(m, 'extendedTextMessage.contextInfo.participant')."""
    for part in dotted.split("."):
        obj = getattr(obj, part, None)
        if obj is None:
            return default
    return obj


def _jid_str(jid: Any) -> str:
    user, server = _attr(jid, "User"), _attr(jid, "Server")
    return f"{user}@{server}" if server else str(user)


def _build_jid(jid: str):
    try:
        from neonize.utils import build_jid
    except ImportError:  # older layout
        from neonize.utils.jid import build_jid  # type: ignore
    user, _, server = jid.partition("@")
    return build_jid(user.split(":")[0], server or "s.whatsapp.net")


_SKIP_KINDS = {"messageContextInfo", "senderKeyDistributionMessage", "deviceSentMessage"}


def _payload_meta(ev: Any, m: Any) -> dict:
    """Cheap payload statistics for the anti-bug engine. Never raises."""
    meta: dict = {}
    try:
        meta["proto_size"] = int(m.ByteSize())
    except Exception:  # noqa: BLE001
        pass
    try:
        kinds = [f.name for f, _ in m.ListFields() if f.name not in _SKIP_KINDS]
        if kinds:
            meta["kind"] = kinds[0]
    except Exception:  # noqa: BLE001
        pass
    try:
        mentions = 0
        for path in ("extendedTextMessage", "imageMessage", "videoMessage", "documentMessage", "audioMessage",
                     "stickerMessage", "buttonsMessage", "listMessage", "templateMessage"):
            ctx = _attr(m, f"{path}.contextInfo", None)
            if ctx is not None:
                mentions = max(mentions, len(getattr(ctx, "mentionedJID", None) or getattr(ctx, "mentionedJid", None) or []))
        meta["mentions"] = mentions
        vcard = _attr(m, "contactMessage.vcard", "") or ""
        arr = _attr(m, "contactsArrayMessage.contacts", None) or []
        meta["vcard_len"] = len(vcard) + sum(len(getattr(c, "vcard", "") or "") for c in arr)
    except Exception:  # noqa: BLE001
        pass
    return meta


def parse_minimal(ev: Any, error: Exception) -> Optional[Incoming]:
    """Last-resort parse for events that break parse_message: keep only who sent it, flag it unparseable."""
    try:
        src = _attr(ev, "Info.MessageSource", None)
        chat = _jid_str(_attr(src, "Chat", None))
        sender_jid = _attr(src, "Sender", None)
        sender = _jid_str(sender_jid)
        number_user = _attr(sender_jid, "User")
        if _attr(sender_jid, "Server") == "lid":
            alt = _attr(src, "SenderAlt", None)
            if _attr(alt, "User"):
                sender, number_user = _jid_str(alt), _attr(alt, "User")
        number = re.sub(r"\D", "", str(number_user).split(":")[0])
        if not chat or not number or chat.endswith("@broadcast") or chat.endswith("@newsletter"):
            return None
        return Incoming(chat=chat, sender=sender, sender_number=number, text="",
                        message_id=str(_attr(ev, "Info.ID", "") or ""), raw=ev,
                        meta={"unparseable": True, "error": f"{type(error).__name__}: {error}"})
    except Exception:  # noqa: BLE001
        return None


def parse_message(ev: Any) -> Optional[Incoming]:
    """Convert a neonize MessageEv into an Incoming (None for things we ignore)."""
    src = _attr(ev, "Info.MessageSource", None)
    if src is None:
        return None
    chat = _jid_str(_attr(src, "Chat", None))
    if chat.endswith("@broadcast") or chat.endswith("@newsletter"):
        return None  # status updates / channels
    sender_jid = _attr(src, "Sender", None)
    sender = _jid_str(sender_jid)
    number_user = _attr(sender_jid, "User")
    # WhatsApp may identify senders by an anonymous "LID"; prefer the real phone-number JID when given.
    if _attr(sender_jid, "Server") == "lid":
        alt = _attr(src, "SenderAlt", None)
        if _attr(alt, "User"):
            sender, number_user = _jid_str(alt), _attr(alt, "User")
    number = re.sub(r"\D", "", str(number_user).split(":")[0])

    m = _attr(ev, "Message", None)
    text = (
        _attr(m, "conversation")
        or _attr(m, "extendedTextMessage.text")
        or _attr(m, "imageMessage.caption")
        or _attr(m, "videoMessage.caption")
        or ""
    )
    quoted = _attr(m, "extendedTextMessage.contextInfo.participant") or None
    return Incoming(
        chat=chat,
        sender=sender,
        sender_number=number,
        text=text,
        push_name=_attr(ev, "Info.Pushname"),
        quoted_participant=quoted,
        message_id=str(_attr(ev, "Info.ID", "") or ""),
        raw=ev,
        meta=_payload_meta(ev, m) if m is not None else {},
    )


class WASession:
    """One linked WhatsApp account."""

    def __init__(self, number: str, client: Any, loop: asyncio.AbstractEventLoop):
        self.number = number
        self.client = client
        self.loop = loop
        self.connected = threading.Event()

    def _send_sync(self, chat: str, text: str, quoted: Any) -> None:
        if quoted is not None:
            try:
                self.client.reply_message(text, quoted)
                return
            except Exception as e:  # noqa: BLE001 — fall back to a plain message
                logger.warn(f"quoted reply failed, sending plain: {e}")
        self.client.send_message(_build_jid(chat), text)

    async def send_text(self, chat: str, text: str, quoted: Any = None) -> None:
        from ..format import brand_message
        await asyncio.to_thread(self._send_sync, chat, brand_message(text), quoted)

    async def group_info(self, chat: str):
        return await asyncio.to_thread(self.client.get_group_info, _build_jid(chat))

    async def group_participants(self, chat: str, numbers: list[str], action: str):
        jids = [_build_jid(f"{re.sub(r'\D', '', n)}@s.whatsapp.net") for n in numbers]
        try:
            from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import ParticipantChangeType
            action_value = getattr(ParticipantChangeType, action.upper())
        except (ImportError, AttributeError):
            action_value = action
        return await asyncio.to_thread(self.client.update_group_participants, _build_jid(chat), jids, action_value)

    async def delete_message(self, chat: str, sender: str, message_id: str):
        if not message_id:
            raise RuntimeError("WhatsApp message ID unavailable; cannot delete this message")
        return await asyncio.to_thread(self.client.revoke_message, _build_jid(chat), _build_jid(sender), message_id)

    # ── anti-bug primitives (best effort: neonize's API differs between versions) ──
    def _call_first(self, names: tuple[str, ...], *variants: tuple):
        """Call the first client method that exists, trying each argument variant until one is accepted."""
        last: Exception | None = None
        for name in names:
            method = getattr(self.client, name, None)
            if not callable(method):
                continue
            for args in variants:
                try:
                    return method(*args)
                except TypeError as exc:  # wrong signature for this neonize version → try next variant
                    last = exc
        raise NotImplementedError(f"no compatible client method ({'/'.join(names)})" + (f": {last}" if last else ""))

    def _block_sync(self, jid: str, block: bool) -> None:
        target = _build_jid(jid)
        action = None
        for mod in ("neonize.utils.enum", "neonize.utils", "neonize.proto.Neonize_pb2"):
            try:
                import importlib
                enum = getattr(importlib.import_module(mod), "BlocklistAction", None)
            except ImportError:
                continue
            if enum is not None:
                action = getattr(enum, "BLOCK" if block else "UNBLOCK", None)
                if action is not None:
                    break
        variants = ((target, action),) if action is not None else ()
        variants += ((target, "block" if block else "unblock"),)
        self._call_first(("update_blocklist", "update_block_list"), *variants)

    async def block_contact(self, jid: str) -> None:
        """Block a contact on WhatsApp (account-level; syncs to the phone)."""
        await asyncio.to_thread(self._block_sync, jid, True)

    async def unblock_contact(self, jid: str) -> None:
        await asyncio.to_thread(self._block_sync, jid, False)

    async def delete_for_me(self, chat: str, sender: str, message_id: str) -> None:
        """Remove one inbound message from this account's chat view (not for the sender)."""
        await asyncio.to_thread(self._call_first, ("delete_message_for_me", "delete_for_me"),
                                (_build_jid(chat), _build_jid(sender), message_id))

    async def delete_chat(self, chat: str) -> None:
        """Delete a whole chat from this account (used to wipe a blocked attacker's thread)."""
        await asyncio.to_thread(self._call_first, ("delete_chat", "clear_chat", "chat_delete"), (_build_jid(chat),))

    async def send_contact(self, chat: str, name: str, number: str):
        """Send a native WhatsApp contact card through Neonize."""
        vcard = f"BEGIN:VCARD\nVERSION:3.0\nFN:{name}\nTEL;TYPE=CELL:+{number}\nEND:VCARD"
        return await asyncio.to_thread(self.client.send_contact, _build_jid(chat), name, vcard)

    async def send_image(self, chat: str, path: str, caption: str = ""):
        """Send an image as WhatsApp media, using the installed Neonize API when available."""
        from pathlib import Path
        file_path = Path(path)
        def _send():
            for name in ("send_image", "send_photo", "send_picture"):
                method = getattr(self.client, name, None)
                if callable(method):
                    for args, kwargs in (((_build_jid(chat), str(file_path)), {"caption": caption}),
                                         ((_build_jid(chat), str(file_path), caption), {})):
                        try:
                            return method(*args, **kwargs)
                        except TypeError:
                            continue
            # Fallback keeps the asset deliverable on Neonize versions without image helpers.
            import mimetypes
            return self.client.send_document(_build_jid(chat), str(file_path),
                filename=file_path.name, mimetype=mimetypes.guess_type(file_path.name)[0] or "image/png",
                caption=caption, title=file_path.name)
        return await asyncio.to_thread(_send)

    async def send_audio(self, chat: str, path: str, caption: str = ""):
        """Send the XCORE soundtrack as audio media where the installed API supports it."""
        from pathlib import Path
        file_path = Path(path)
        def _send():
            for name in ("send_audio", "send_voice", "send_music"):
                method = getattr(self.client, name, None)
                if callable(method):
                    for args, kwargs in (((_build_jid(chat), str(file_path)), {"caption": caption}),
                                         ((_build_jid(chat), str(file_path), caption), {})):
                        try:
                            return method(*args, **kwargs)
                        except TypeError:
                            continue
            # Compatibility fallback for library versions exposing document upload only.
            import mimetypes
            return self.client.send_document(_build_jid(chat), str(file_path),
                filename=file_path.name, mimetype=mimetypes.guess_type(file_path.name)[0] or "audio/mpeg",
                caption=caption, title=file_path.name)
        return await asyncio.to_thread(_send)

    async def send_file(self, chat: str, path: str, caption: str = ""):
        """Send a downloaded file as a WhatsApp document."""
        import mimetypes
        from pathlib import Path
        file_path = Path(path)
        mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        return await asyncio.to_thread(
            self.client.send_document, _build_jid(chat), str(file_path),
            filename=file_path.name, mimetype=mime, caption=caption, title=file_path.name,
        )

    async def download_media(self, raw_event: Any):
        """Download media from a replied-to or captioned WhatsApp image."""
        message = _attr(raw_event, "Message", raw_event)
        quoted = _attr(message, "extendedTextMessage.contextInfo.quotedMessage", None)
        if quoted is not None:
            message = quoted
        method = getattr(self.client, "download_any", None)
        if not callable(method):
            raise RuntimeError("Neonize download_any is unavailable")
        result = await asyncio.to_thread(method, message)
        if isinstance(result, (bytes, bytearray)):
            return bytes(result)
        if isinstance(result, str):
            return result
        raise RuntimeError("Neonize returned an unsupported media result")

    async def user_devices(self, number: str):
        method = getattr(self.client, "get_user_devices", None)
        if not callable(method):
            raise RuntimeError("This Neonize version does not expose device lookup")
        return await asyncio.to_thread(method, _build_jid(f"{number}@s.whatsapp.net"))

    async def is_on_whatsapp(self, number: str):
        method = getattr(self.client, "is_on_whatsapp", None)
        if not callable(method):
            raise RuntimeError("This Neonize version does not expose WhatsApp registration checks")
        jid = _build_jid(f"{re.sub(r'\D', '', number)}@s.whatsapp.net")
        return await asyncio.to_thread(method, [jid])

    def disconnect(self) -> None:
        try:
            self.client.disconnect()
        except Exception:  # noqa: BLE001
            pass


CONNECT_WAIT = 6.0  # seconds to wait for an existing session to come online before requesting a pairing code
PAIR_ATTEMPTS = 8
PAIR_RETRY_DELAY = 2.0

sessions: dict[str, WASession] = {}
_disabled_sessions: set[str] = set()


def list_sessions() -> list[str]:
    return list(sessions.keys())


def disconnect_session(number: str) -> bool:
    """Disconnect one account and remove this bot's local linked-device credentials."""
    clean = re.sub(r"\D", "", str(number))
    sess = sessions.pop(clean, None)
    folder = config.get().paths.sessions / clean
    if not sess and not folder.exists():
        return False
    _disabled_sessions.add(clean)
    db.set_session_disabled(clean, True)
    if sess:
        sess.connected.clear()
        sess.disconnect()
    # Remove only this account's local linked-device credentials; this does not revoke
    # other linked devices from the WhatsApp mobile app.
    try:
        if folder.exists():
            shutil.rmtree(folder)
    except OSError as exc:
        logger.error(f"Could not remove local session for +{clean}:", exc)
        return False
    db.remove_paired(clean)
    db.unlink_telegram_number(clean)
    return True


def get_session(number: str) -> Optional[WASession]:
    return sessions.get(re.sub(r"\D", "", str(number)))


def _on_task_done(fut) -> None:
    try:
        fut.result()
    except Exception as e:  # noqa: BLE001
        logger.error("message handler:", e)


async def create_session(number: str, request_pairing: bool = False) -> dict:
    """Start (or resume) a WhatsApp session.

    Returns {"session": WASession, "pairingCode": str|None, "error": str|None}.
    If the account is not linked yet, a pairing code is requested (enter it in
    WhatsApp → Linked devices → Link with phone number).
    """
    from neonize import events as ne
    from neonize.client import NewClient

    clean = re.sub(r"\D", "", str(number))
    _disabled_sessions.discard(clean)
    db.set_session_disabled(clean, False)
    existing = sessions.get(clean)
    if existing and existing.connected.is_set():
        return {"session": existing, "pairingCode": None, "error": None}
    if not clean or not 7 <= len(clean) <= 15:
        return {"session": None, "pairingCode": None, "error": "invalid phone number"}
    folder = config.get().paths.sessions / clean
    folder.mkdir(parents=True, exist_ok=True)
    loop = asyncio.get_running_loop()

    client = NewClient(str(folder / "session.sqlite3"))
    sess = WASession(clean, client, loop)

    @client.event(ne.ConnectedEv)
    def _connected(_c, _ev):
        if clean in _disabled_sessions:
            try:
                client.disconnect()
            except Exception:
                pass
            return
        sessions[clean] = sess
        db.add_paired(clean)
        sess.connected.set()
        logger.ok(f"Session open: +{clean}")

    @client.event(ne.MessageEv)
    def _message(_c, ev):
        try:
            msg = parse_message(ev)
        except Exception as e:  # noqa: BLE001
            logger.error("parse_message:", e)
            msg = parse_minimal(ev, e)  # a payload that breaks parsing is itself an anti-bug signal
        if msg:
            asyncio.run_coroutine_threadsafe(handle_message(sess, msg), loop).add_done_callback(_on_task_done)

    disconnected = getattr(ne, "DisconnectedEv", None)
    if disconnected:
        @client.event(disconnected)
        def _disconnected(_c, _ev):
            sess.connected.clear()
            sessions.pop(clean, None)
            logger.warn(f"Disconnected: +{clean} (the library reconnects automatically)")

    logged_out = getattr(ne, "LoggedOutEv", None)
    if logged_out:
        @client.event(logged_out)
        def _logged_out(_c, _ev):
            sessions.pop(clean, None)
            logger.error(f"Logged out: +{clean} — delete sessions/{clean} and pair again")

    threading.Thread(target=client.connect, name=f"wa-{clean}", daemon=True).start()

    result: dict = {"session": sess, "pairingCode": None, "error": None}
    if not request_pairing:
        return result

    # Already-linked sessions connect within a few seconds; otherwise ask for a pairing code.
    if await asyncio.to_thread(sess.connected.wait, CONNECT_WAIT):
        return result
    last_err = "no response from WhatsApp"
    for _ in range(PAIR_ATTEMPTS):
        try:
            code = await asyncio.to_thread(client.PairPhone, clean, True)
            result["pairingCode"] = str(code)
            return result
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            if sess.connected.is_set():  # linked after all
                return result
            await asyncio.sleep(PAIR_RETRY_DELAY)
    result["error"] = f"{last_err} (a QR code may have been printed in the console instead)"
    return result


def disconnect_all() -> None:
    for s in list(sessions.values()):
        s.disconnect()
    sessions.clear()
