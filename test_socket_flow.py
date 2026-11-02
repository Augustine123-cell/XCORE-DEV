"""Exercises whatsapp/socket.py against a fake `neonize` that mimics the API shape the adapter targets."""
import asyncio
import sys
import types

from tests.base import Base

N = types.SimpleNamespace


def install_fake_neonize(auto_connect: bool):
    ev = types.ModuleType("neonize.events")
    for name in ("ConnectedEv", "MessageEv", "DisconnectedEv", "LoggedOutEv"):
        setattr(ev, name, type(name, (), {}))

    class NewClient:
        instances: list = []

        def __init__(self, path):
            self.path, self.handlers, self.sent, self.replied = path, {}, [], []
            self.pair_calls = 0
            NewClient.instances.append(self)

        def event(self, cls):
            def deco(fn):
                self.handlers[cls] = fn
                return fn

            return deco

        def connect(self):
            if auto_connect:
                self.handlers[ev.ConnectedEv](self, None)

        def PairPhone(self, phone, push):
            self.pair_calls += 1
            if self.pair_calls < 2:
                raise RuntimeError("not connected yet")
            return "ABCD-1234"

        def send_message(self, jid, text):
            self.sent.append((jid, text))

        def reply_message(self, text, quoted):
            self.replied.append((text, quoted))

        def disconnect(self):
            self.disconnected = True

    client = types.ModuleType("neonize.client")
    client.NewClient = NewClient
    utils = types.ModuleType("neonize.utils")
    utils.build_jid = lambda user, server="s.whatsapp.net": N(User=user, Server=server)
    pkg = types.ModuleType("neonize")
    pkg.events, pkg.client, pkg.utils = ev, client, utils
    sys.modules.update({"neonize": pkg, "neonize.events": ev, "neonize.client": client, "neonize.utils": utils})
    return NewClient, ev


class SocketFlowTests(Base):
    def setUp(self):
        super().setUp()
        from xcore.whatsapp import socket as wa

        self.wa = wa
        wa.CONNECT_WAIT, wa.PAIR_RETRY_DELAY = 0.3, 0.01
        wa.sessions.clear()
        self.addCleanup(lambda: [sys.modules.pop(k, None) for k in ("neonize", "neonize.events", "neonize.client", "neonize.utils")])

    async def test_existing_session_connects_without_pairing(self):
        Client, ev = install_fake_neonize(auto_connect=True)
        res = await self.wa.create_session(self.OWNER, request_pairing=True)
        self.assertIsNone(res["pairingCode"])
        self.assertIsNone(res["error"])
        self.assertEqual(self.wa.list_sessions(), [self.OWNER])
        self.assertTrue(Client.instances[-1].path.endswith(f"{self.OWNER}/session.sqlite3"))
        from xcore import db

        self.assertTrue(db.is_paired(self.OWNER))
        # inbound message → command → quoted reply through the client
        c = Client.instances[-1]
        N_ = types.SimpleNamespace
        jid = N_(User=self.OWNER, Server="s.whatsapp.net")
        raw = N_(Info=N_(MessageSource=N_(Chat=jid, Sender=jid, SenderAlt=None), Pushname="Me"),
                 Message=N_(conversation="®ping", extendedTextMessage=N_(text="", contextInfo=N_(participant="")),
                            imageMessage=N_(caption=""), videoMessage=N_(caption="")))
        c.handlers[ev.MessageEv](c, raw)
        for _ in range(50):
            await asyncio.sleep(0.02)
            if c.replied:
                break
        self.assertEqual(len(c.replied), 1)
        self.assertIn("🏓 PONG", c.replied[0][0])
        self.assertIn("Developer: 𝙳𝙴𝚅 𝙰𝚄𝚂𝚃𝙸𝙽", c.replied[0][0])
        self.assertIs(c.replied[0][1], raw)
        # unquoted send (used by the monitor) builds a JID
        await self.wa.sessions[self.OWNER].send_text("120363@g.us", "hello")
        self.assertEqual((c.sent[0][0].User, c.sent[0][0].Server), ("120363", "g.us"))
        self.assertIn("hello", c.sent[0][1])
        self.assertIn("╭━━━〔 𝚇𝙲𝙾𝚁𝙴® 〕━━━╮", c.sent[0][1])
        self.assertIn("Developer: 𝙳𝙴𝚅 𝙰𝚄𝚂𝚃𝙸𝙽", c.sent[0][1])
        # quoted reply failing falls back to a plain send
        c.reply_message = lambda *a: (_ for _ in ()).throw(RuntimeError("x"))
        await self.wa.sessions[self.OWNER].send_text(f"{self.OWNER}@s.whatsapp.net", "fallback", quoted=raw)
        self.assertIn("fallback", c.sent[-1][1])
        self.assertIn("Developer: 𝙳𝙴𝚅 𝙰𝚄𝚂𝚃𝙸𝙽", c.sent[-1][1])
        # disconnect / logout events drop the session
        c.handlers[ev.DisconnectedEv](c, None)
        self.assertEqual(self.wa.list_sessions(), [])

    async def test_new_session_requests_pairing_code_with_retry(self):
        Client, _ = install_fake_neonize(auto_connect=False)
        res = await self.wa.create_session(self.OWNER, request_pairing=True)
        self.assertEqual(res["pairingCode"], "ABCD-1234")
        self.assertEqual(Client.instances[-1].pair_calls, 2)  # first attempt failed, retried
        self.assertEqual(self.wa.list_sessions(), [])  # not online until the user enters the code

    async def test_pairing_failure_is_reported_not_raised(self):
        Client, _ = install_fake_neonize(auto_connect=False)
        self.wa.PAIR_ATTEMPTS = 2
        self.addCleanup(setattr, self.wa, "PAIR_ATTEMPTS", 8)
        Client.PairPhone = lambda self, p, push: (_ for _ in ()).throw(RuntimeError("nope"))
        res = await self.wa.create_session(self.OWNER, request_pairing=True)
        self.assertIsNone(res["pairingCode"])
        self.assertIn("nope", res["error"])
