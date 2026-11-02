"""Library-agnostic message objects. Commands only ever see these, never the WhatsApp library."""
from dataclasses import dataclass, field
from typing import Any, Protocol


def norm_jid(jid: str) -> str:
    """'234…:12@s.whatsapp.net' → '234…@s.whatsapp.net' (device suffix removed)."""
    user, _, server = str(jid).partition("@")
    return f"{user.split(':')[0]}@{server}" if server else user.split(":")[0]


class Session(Protocol):
    number: str

    async def send_text(self, chat: str, text: str, quoted: Any = None) -> None: ...


@dataclass
class Incoming:
    """A normalised inbound message, produced by the socket adapter."""

    chat: str  # chat JID string, e.g. 234…@s.whatsapp.net / …@g.us
    sender: str  # sender JID string
    sender_number: str  # digits only
    text: str = ""
    push_name: str = ""
    quoted_participant: str | None = None  # JID of the author of the message being replied to
    message_id: str = ""  # WhatsApp message ID, used for safe moderation deletion
    raw: Any = None  # the library's native event, used only for quoting replies
    meta: dict = field(default_factory=dict)  # payload stats from the transport: kind, proto_size, mentions, vcard_len, unparseable


@dataclass
class Ctx:
    session: Session
    msg: Incoming
    args: list[str]
    is_owner: bool
    is_sudo: bool
    cmd: str = ""
    extra: dict = field(default_factory=dict)

    # convenience accessors (mirror the Node ctx fields)
    @property
    def chat(self) -> str:
        return self.msg.chat

    @property
    def sender(self) -> str:
        return self.msg.sender

    @property
    def sender_number(self) -> str:
        return self.msg.sender_number

    @property
    def text(self) -> str:
        return self.msg.text

    async def reply(self, text: str) -> None:
        from ..format import BRAND_FOOTER

        if "Developer: 𝙳𝙴𝚅 𝙰𝚄𝚂𝚃𝙸𝙽" not in text:
            text = f"{text.rstrip()}\n\n{BRAND_FOOTER}"
        await self.session.send_text(self.msg.chat, text, quoted=self.msg.raw)
