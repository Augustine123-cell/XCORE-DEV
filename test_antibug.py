"""Anti-bug engine: detection scoring, auto-block, purge, shield, effectiveness, commands."""
import asyncio
import sys
import types

from tests.base import Base

OWNER = "2348130300032"
ATTACKER = "2349011112222"
GROUP = "120363000000000000@g.us"
N = types.SimpleNamespace


class BugSession:
    """Fake session with every anti-bug primitive; individual abilities can be switched off per test."""
    number = OWNER

    def __init__(self, members=None, block="ok", dm_delete="ok", chat_wipe=False):
        self.sent, self.blocked, self.deleted, self.dm_deleted, self.chats_wiped = [], [], [], [], []
        self.members, self.block_mode, self.dm_delete_mode, self.chat_wipe = members or [], block, dm_delete, chat_wipe

    async def send_text(self, chat, text, quoted=None):
        self.sent.append((chat, text))

    async def group_info(self, chat):
        return {"Participants": self.members}

    async def delete_message(self, chat, sender, message_id):
        self.deleted.append((chat, sender, message_id))

    async def block_contact(self, jid):
        if self.block_mode == "fail":
            raise RuntimeError("server said no")
        if self.block_mode == "unsupported":
            raise NotImplementedError("no blocklist api")
        await asyncio.sleep(0)
        self.blocked.append(jid)

    async def unblock_contact(self, jid):
        self.unblocked = getattr(self, "unblocked", []) + [jid]

    async def delete_for_me(self, chat, sender, message_id):
        if self.dm_delete_mode == "unsupported":
            raise NotImplementedError("nope")
        self.dm_deleted.append((chat, message_id))

    async def delete_chat(self, chat):
        if not self.chat_wipe:
            raise NotImplementedError("nope")
        self.chats_wiped.append(chat)


def incoming(text="hello", sender=ATTACKER, chat=None, mid="m1", meta=None, name="Attacker"):
    from xcore.whatsapp.context import Incoming
    return Incoming(chat=chat or f"{sender}@s.whatsapp.net", sender=f"{sender}@s.whatsapp.net", sender_number=sender,
                    text=text, message_id=mid, push_name=name, meta=meta or {})


class AnalyzerTests(Base):
    def keys(self, text, meta=None):
        from xcore.whatsapp.security import analyze_payload
        return {s.key: s.points for s in analyze_payload(text, meta)}

    def test_normal_messages_are_clean(self):
        samples = ["hello", "®menu", "Good morning 😀🙏 how are you?", "a " * 1500,
                   "مرحبا كيف حالك؟ " * 20, "नमस्ते आप कैसे हैं " * 30, "check https://a.com https://b.com https://c.com",
                   "😂" * 200, "Line one\nLine two\n\tindented", "Ẹ kú àárọ̀, báwo ni? " * 40]
        for text in samples:
            self.assertEqual(self.keys(text), {}, text[:30])

    def test_oversized(self):
        self.assertEqual(self.keys("A b " * 3500)["oversized"], 60)   # ~14 KB
        self.assertEqual(self.keys("A b " * 6000)["oversized"], 90)   # ~24 KB

    def test_control_invisible_combining(self):
        self.assertEqual(self.keys("hi" + "\x00" * 25)["controls"], 55)
        self.assertEqual(self.keys("\x01" * 250)["controls"], 85)
        self.assertEqual(self.keys("hi" + "\u202e" * 40)["invisible"], 55)
        self.assertEqual(self.keys("\u200b" * 400)["invisible"], 85)
        self.assertEqual(self.keys("a" + "\u0301" * 120)["combining"], 70)
        self.assertEqual(self.keys("a" + "\u0301" * 500)["combining"], 85)

    def test_runs_links_mentions(self):
        self.assertIn("charrun", self.keys("x" * 3500))
        self.assertIn("token", self.keys("ab" * 2500))
        self.assertEqual(self.keys(" ".join(["https://x.io"] * 9))["urls"], 60)
        self.assertEqual(self.keys(" ".join(f"@2348000000{i:03d}" for i in range(15)))["mentions-text"], 60)

    def test_transport_meta(self):
        self.assertEqual(self.keys("", {"mentions": 250})["mentions-meta"], 60)
        self.assertEqual(self.keys("", {"mentions": 1500})["mentions-meta"], 85)
        self.assertEqual(self.keys("", {"proto_size": 70_000})["proto"], 50)
        self.assertEqual(self.keys("", {"proto_size": 300_000})["proto"], 85)
        self.assertEqual(self.keys("", {"vcard_len": 30_000})["vcard"], 60)
        self.assertEqual(self.keys("", {"unparseable": True, "error": "x"})["unparseable"], 45)
        self.assertEqual(self.keys("", {"kind": "nativeFlowMessage"})["kind"], 20)
        self.assertEqual(self.keys("", {"kind": "imageMessage"}), {})

    def test_strength_levels_and_combination(self):
        from xcore.whatsapp.security import combine, level_for, strength_bar
        self.assertEqual(combine([]), 0)
        self.assertEqual(combine([60]), 60)
        self.assertEqual(combine([60, 60]), 81)
        self.assertEqual(combine([90, 90, 90]), 100)
        self.assertEqual([level_for(x) for x in (10, 45, 70, 95)], ["LOW", "MEDIUM", "HIGH", "CRITICAL"])
        self.assertEqual(strength_bar(80), "▰" * 8 + "▱" * 2)

    def test_effectiveness_math(self):
        from xcore.whatsapp.security import effectiveness
        on = {"autoblock": True, "autopurge": True}
        self.assertEqual(effectiveness("ok", 5, 5, True, on), 100)
        self.assertEqual(effectiveness("ok", 0, 5, True, on), 70)
        self.assertEqual(effectiveness("failed", 5, 5, True, on), 50)
        self.assertEqual(effectiveness("failed", 0, 5, True, on), 20)
        self.assertEqual(effectiveness("ok", 3, 6, True, on), 85)
        self.assertEqual(effectiveness("skipped", 5, 5, True, {"autoblock": False, "autopurge": True}), 100)


class CounterTests(Base):
    async def asyncSetUp(self):
        from xcore.whatsapp.security import reset_runtime_state
        reset_runtime_state()

    async def scan(self, session, msg):
        from xcore.whatsapp.security import inspect_message
        return await inspect_message(session, msg)

    async def test_dm_bug_is_blocked_purged_and_reported_at_100(self):
        from xcore import db
        s = BugSession(chat_wipe=False)
        for i in range(3):
            assert await self.scan(s, incoming("hi", mid=f"pre{i}")) is None
        report = await self.scan(s, incoming("A b " * 6000, mid="bug"))
        self.assertTrue(report.countered)
        self.assertEqual(report.strength, 90)
        self.assertEqual(s.blocked, [f"{ATTACKER}@s.whatsapp.net"])
        self.assertEqual(len(s.dm_deleted), 4)                     # 3 earlier + the bug itself
        self.assertEqual(report.outcome["effectiveness"], 100)
        self.assertIsNotNone(db.antibug_shield_get(ATTACKER))
        self.assertTrue(db.antibug_shield_get(ATTACKER)["waBlocked"])
        self.assertNotIn(f"{ATTACKER}@s.whatsapp.net", [c for c, _ in s.sent])   # never message the attacker
        alert = next(t for c, t in s.sent if c == f"{OWNER}@s.whatsapp.net")
        self.assertIn("Bug strength: 90/100", alert)
        self.assertIn("CRITICAL", alert)
        self.assertIn("Counter effectiveness: 100%", alert)
        log = db.list_bugs()[0]
        self.assertEqual((log["reason"], log["action"], log["blocked"], log["deleted"]), ("oversized payload", "countered", True, 4))

    async def test_dm_without_delete_support_is_honest_about_it(self):
        s = BugSession(dm_delete="unsupported")
        report = await self.scan(s, incoming("A b " * 6000))
        self.assertEqual(report.outcome["effectiveness"], 70)      # block 50 + shield 20, purge 0/30
        self.assertIn("delete unsupported", report.outcome["purge_note"])

    async def test_chat_wipe_fallback_counts_as_purged(self):
        s = BugSession(dm_delete="unsupported", chat_wipe=True)
        report = await self.scan(s, incoming("A b " * 6000))
        self.assertEqual(s.chats_wiped, [f"{ATTACKER}@s.whatsapp.net"])
        self.assertEqual(report.outcome["effectiveness"], 100)

    async def test_block_failure_lowers_effectiveness_but_shield_still_holds(self):
        from xcore import db
        for i, (mode, note) in enumerate((("fail", "server said no"), ("unsupported", "no blocklist api"))):
            from xcore.whatsapp.security import reset_runtime_state
            reset_runtime_state()
            attacker = f"234901111{i:04d}"
            s = BugSession(block=mode)
            report = await self.scan(s, incoming("A b " * 6000, sender=attacker))
            self.assertEqual(report.outcome["block"], "failed" if mode == "fail" else "unsupported")
            self.assertEqual(report.outcome["effectiveness"], 50)  # purge 30 + shield 20
            self.assertFalse(db.antibug_shield_get(attacker)["waBlocked"])
            self.assertIn(note, next(t for _, t in s.sent))

    async def test_session_without_any_primitives_still_counters(self):
        class Bare:
            number = OWNER
            def __init__(self): self.sent = []
            async def send_text(self, chat, text, quoted=None): self.sent.append((chat, text))
        s = Bare()
        report = await self.scan(s, incoming("A b " * 6000))
        self.assertTrue(report.countered)
        self.assertEqual(report.outcome["effectiveness"], 20)      # only the bot shield is confirmed

    async def test_shield_drops_followups_and_pipeline_stops(self):
        from xcore.whatsapp.handler import handle_message
        s = BugSession()
        await self.scan(s, incoming("A b " * 6000))
        before = len(s.sent)
        await handle_message(s, incoming("®ping", mid="after"))
        self.assertEqual(len(s.sent), before)                      # no command reply
        self.assertIn(("%s@s.whatsapp.net" % ATTACKER, "after"), s.dm_deleted)

    async def test_dm_flood_threshold(self):
        s = BugSession()
        for i in range(9):
            self.assertIsNone(await self.scan(s, incoming(f"msg {i}", mid=f"f{i}")))
        report = await self.scan(s, incoming("msg 9", mid="f9"))
        self.assertTrue(report.countered)
        self.assertEqual(report.primary, "message flood")
        self.assertEqual(len(s.dm_deleted), 10)

    async def test_concurrent_burst_counters_once(self):
        s = BugSession()
        results = await asyncio.gather(*(self.scan(s, incoming("A b " * 6000, mid=f"c{i}")) for i in range(40)))
        self.assertEqual(len(s.blocked), 1)
        self.assertEqual(sum(1 for r in results if r and r.countered), 1)
        self.assertTrue(all(r is not None and r.handled for r in results))
        self.assertEqual(len([1 for c, _ in s.sent if c == f"{OWNER}@s.whatsapp.net"]), 1)

    async def test_unparseable_payload_needs_two_strikes(self):
        s = BugSession()
        meta = {"unparseable": True, "error": "KeyError: x"}
        self.assertIsNone(await self.scan(s, incoming("", mid="u1", meta=meta)))
        report = await self.scan(s, incoming("", mid="u2", meta=meta))
        self.assertTrue(report.countered)
        self.assertEqual(report.primary, "repeated unparseable payloads")

    async def test_exempt_numbers_are_logged_never_blocked(self):
        from xcore import db
        db.add_sudo("2348000000055")
        db.antibug_trust("2348000000066")
        for who in (OWNER, "2348000000055", "2348000000066"):
            s = BugSession()
            self.assertIsNone(await self.scan(s, incoming("A b " * 6000, sender=who)))
            self.assertEqual((s.blocked, s.dm_deleted, s.sent), ([], [], []))
            self.assertEqual(db.list_bugs()[0]["action"], "exempt")
            self.assertIsNone(db.antibug_shield_get(who))

    async def test_group_bug_blocks_deletes_mutes_and_alerts_group(self):
        from xcore import db
        members = [{"JID": f"{OWNER}@s.whatsapp.net", "IsAdmin": True}, {"JID": f"{ATTACKER}@s.whatsapp.net", "IsAdmin": False}]
        s = BugSession(members=members)
        report = None
        for i in range(7):
            report = await self.scan(s, incoming("spam", chat=GROUP, mid=f"g{i}"))
        self.assertTrue(report.countered)
        self.assertEqual(len(s.deleted), 7)
        self.assertEqual(s.blocked, [f"{ATTACKER}@s.whatsapp.net"])
        self.assertIsNotNone(db.get_mute(GROUP, ATTACKER))
        self.assertIn(GROUP, [c for c, _ in s.sent])
        self.assertEqual(report.outcome["effectiveness"], 100)

    async def test_group_without_admin_rights_reports_partial(self):
        members = [{"JID": f"{OWNER}@s.whatsapp.net", "IsAdmin": False}, {"JID": f"{ATTACKER}@s.whatsapp.net", "IsAdmin": False}]
        s = BugSession(members=members)
        report = await self.scan(s, incoming("A b " * 6000, chat=GROUP))
        self.assertEqual(s.deleted, [])
        self.assertEqual(report.outcome["effectiveness"], 70)
        self.assertIn("bot is not a group admin", report.outcome["purge_note"])

    async def test_group_admin_attacker_is_not_touched(self):
        members = [{"JID": f"{OWNER}@s.whatsapp.net", "IsAdmin": True}, {"JID": f"{ATTACKER}@s.whatsapp.net", "IsAdmin": True}]
        s = BugSession(members=members)
        self.assertIsNone(await self.scan(s, incoming("A b " * 6000, chat=GROUP)))
        self.assertEqual(s.blocked, [])

    async def test_settings_switches(self):
        from xcore import db
        db.set_antibug_option("autoblock", False)
        s = BugSession()
        report = await self.scan(s, incoming("A b " * 6000))
        self.assertEqual((s.blocked, report.outcome["block"], report.outcome["effectiveness"]), ([], "skipped", 100))
        db.set_antibug_option("enabled", False)
        s2 = BugSession()
        self.assertIsNone(await self.scan(s2, incoming("A b " * 6000, sender="2349022223333")))
        self.assertEqual(s2.blocked, [])

    async def test_sensitivity_threshold(self):
        from xcore import db
        s = BugSession()
        text = "a" + "\u0301" * 120                                 # strength 70
        db.set_antibug_option("threshold", 75)
        self.assertIsNone(await self.scan(s, incoming(text)))
        db.set_antibug_option("threshold", 45)
        self.assertTrue((await self.scan(s, incoming(text, mid="m2"))).countered)


class AntibugCommandTests(Base):
    async def run_cmd(self, text, sender=OWNER):
        from xcore.whatsapp.handler import handle_message
        from xcore.whatsapp.security import reset_runtime_state
        reset_runtime_state()
        s = BugSession()
        await handle_message(s, incoming(text, sender=sender, name="Owner"))
        return s, (s.sent[-1][1] if s.sent else "")

    async def test_status_on_off_level_trust_list_unblock(self):
        from xcore import db
        _, out = await self.run_cmd("®antibug status")
        self.assertIn("Protection: ON", out)
        _, out = await self.run_cmd("®antibug level high")
        self.assertEqual(db.antibug_settings()["threshold"], 45)
        _, out = await self.run_cmd("®antibug level bogus")
        self.assertIn("Sensitivity: high", out)
        await self.run_cmd("®antibug block off")
        await self.run_cmd("®antibug purge off")
        s = db.antibug_settings()
        self.assertEqual((s["autoblock"], s["autopurge"]), (False, False))
        await self.run_cmd("®antibug off")
        self.assertFalse(db.antibug_settings()["enabled"])
        await self.run_cmd("®antibug on")
        self.assertTrue(db.antibug_settings()["enabled"])
        await self.run_cmd("®antibug trust +2348010000001")
        self.assertTrue(db.antibug_trusted("2348010000001"))
        await self.run_cmd("®antibug untrust 2348010000001")
        self.assertFalse(db.antibug_trusted("2348010000001"))
        db.antibug_shield_add(ATTACKER, 88, "oversized payload", True)
        _, out = await self.run_cmd("®antibug list")
        self.assertIn(f"+{ATTACKER}", out)
        self.assertIn("WA-blocked", out)
        s, out = await self.run_cmd(f"®antibug unblock {ATTACKER}")
        self.assertIn("removed from the shield", out)
        self.assertIn("WhatsApp block lifted", out)
        self.assertIsNone(db.antibug_shield_get(ATTACKER))

    async def test_log_shows_strength_and_effectiveness_and_is_sudo_only(self):
        from xcore import db
        from xcore.whatsapp.security import inspect_message
        await inspect_message(BugSession(), incoming("A b " * 6000))
        _, out = await self.run_cmd("®antibug")
        self.assertIn("oversized payload", out)
        self.assertIn("90/100", out)
        self.assertIn("effectiveness 100%", out)
        _, out = await self.run_cmd("®antibug status", sender="2348000000077")
        self.assertIn("Sudo-only", out)


class SocketPrimitiveTests(Base):
    def setUp(self):
        super().setUp()
        utils = types.ModuleType("neonize.utils")
        utils.build_jid = lambda user, server="s.whatsapp.net": N(User=user, Server=server)
        enum = types.ModuleType("neonize.utils.enum")
        enum.BlocklistAction = N(BLOCK="BLOCK", UNBLOCK="UNBLOCK")
        pkg = types.ModuleType("neonize")
        sys.modules.update({"neonize": pkg, "neonize.utils": utils, "neonize.utils.enum": enum})

    def make(self, client):
        from xcore.whatsapp.socket import WASession
        return WASession(OWNER, client, asyncio.get_running_loop())

    async def test_block_and_unblock_call_the_client(self):
        calls = []
        client = N(update_blocklist=lambda jid, action: calls.append((jid.User, action)))
        s = self.make(client)
        await s.block_contact(f"{ATTACKER}:12@s.whatsapp.net")
        await s.unblock_contact(f"{ATTACKER}@s.whatsapp.net")
        self.assertEqual(calls, [(ATTACKER, "BLOCK"), (ATTACKER, "UNBLOCK")])

    async def test_block_falls_back_to_second_method_name_and_string_action(self):
        calls = []
        def alt(jid, action):
            if not isinstance(action, str) or action != "block":
                raise TypeError("bad action")
            calls.append(jid.User)
        s = self.make(N(update_block_list=alt))
        await s.block_contact(f"{ATTACKER}@s.whatsapp.net")
        self.assertEqual(calls, [ATTACKER])

    async def test_missing_client_support_raises_not_implemented(self):
        s = self.make(N())
        for coro in (s.block_contact(f"{ATTACKER}@s.whatsapp.net"), s.delete_for_me("a@s.whatsapp.net", "a@s.whatsapp.net", "1"),
                     s.delete_chat("a@s.whatsapp.net")):
            with self.assertRaises(NotImplementedError):
                await coro

    def test_payload_meta_and_minimal_parse(self):
        from xcore.whatsapp.socket import _payload_meta, parse_minimal, parse_message
        field = lambda name: N(name=name)
        m = N(ByteSize=lambda: 90_000, ListFields=lambda: [(field("messageContextInfo"), 1), (field("nativeFlowMessage"), 2)],
              extendedTextMessage=N(contextInfo=N(mentionedJID=["x"] * 300)),
              contactMessage=N(vcard="v" * 25_000))
        meta = _payload_meta(N(), m)
        self.assertEqual((meta["proto_size"], meta["kind"], meta["mentions"], meta["vcard_len"]), (90_000, "nativeFlowMessage", 300, 25_000))
        ev = N(Info=N(ID="ABC", Pushname="x", MessageSource=N(Chat=N(User=ATTACKER, Server="s.whatsapp.net"),
                                                                   Sender=N(User=ATTACKER, Server="s.whatsapp.net"))), Message=m)
        inc = parse_message(ev)
        self.assertEqual(inc.meta["kind"], "nativeFlowMessage")
        mini = parse_minimal(ev, ValueError("boom"))
        self.assertEqual((mini.sender_number, mini.message_id, mini.meta["unparseable"]), (ATTACKER, "ABC", True))
        self.assertIsNone(parse_minimal(N(), ValueError("x")))


class EndToEndSocketTests(Base):
    """Inbound neonize event → parse → anti-bug → real WASession.block_contact → owner alert via the client."""

    def setUp(self):
        super().setUp()
        from xcore.whatsapp import socket as wa
        from xcore.whatsapp.security import reset_runtime_state
        reset_runtime_state()
        self.wa = wa
        wa.sessions.clear()
        self.addCleanup(wa.sessions.clear)  # do not leak a live fake session into other test modules
        self.addCleanup(lambda: [sys.modules.pop(k, None) for k in list(sys.modules) if k == "neonize" or k.startswith("neonize.")])

    async def boot(self):
        from tests.test_socket_flow import install_fake_neonize
        Client, ev = install_fake_neonize(auto_connect=True)
        enum = types.ModuleType("neonize.utils.enum")
        enum.BlocklistAction = N(BLOCK="BLOCK", UNBLOCK="UNBLOCK")
        sys.modules["neonize.utils.enum"] = enum
        await self.wa.create_session(OWNER, request_pairing=True)
        c = Client.instances[-1]
        c.blocked = []
        c.update_blocklist = lambda jid, action: c.blocked.append((jid.User, action))
        return c, ev

    @staticmethod
    def event(text, sender=ATTACKER, mid="EV1"):
        jid = N(User=sender, Server="s.whatsapp.net")
        return N(Info=N(ID=mid, MessageSource=N(Chat=jid, Sender=jid, SenderAlt=None), Pushname="Evil"),
                 Message=N(conversation=text, extendedTextMessage=N(text="", contextInfo=N(participant="")),
                           imageMessage=N(caption=""), videoMessage=N(caption="")))

    async def wait_for(self, predicate):
        for _ in range(100):
            if predicate():
                return
            await asyncio.sleep(0.02)
        self.fail("timed out")

    async def test_bug_message_gets_attacker_blocked_and_owner_alerted(self):
        c, ev = await self.boot()
        c.handlers[ev.MessageEv](c, self.event("A b " * 6000))
        await self.wait_for(lambda: c.sent)
        self.assertEqual(c.blocked, [(ATTACKER, "BLOCK")])
        jid, text = c.sent[-1]
        self.assertEqual(jid.User, OWNER)
        self.assertIn("THREAT COUNTERED", text)
        self.assertIn(f"+{ATTACKER}", text)
        self.assertIn("Bug strength: 90/100", text)
        self.assertIn("blocked on WhatsApp", text)
        self.assertIn("Counter effectiveness: 70%", text)   # fake client has no delete-for-me/chat-wipe → reported honestly

    async def test_payload_that_breaks_parsing_is_still_caught(self):
        c, ev = await self.boot()
        class Exploding:
            Info = N(ID="X", MessageSource=N(Chat=N(User=ATTACKER, Server="s.whatsapp.net"),
                                             Sender=N(User=ATTACKER, Server="s.whatsapp.net"), SenderAlt=None), Pushname="Evil")
            @property
            def Message(self):
                raise RuntimeError("malformed protobuf")
        for _ in range(2):
            c.handlers[ev.MessageEv](c, Exploding())
        await self.wait_for(lambda: c.blocked)
        self.assertEqual(c.blocked, [(ATTACKER, "BLOCK")])

    async def test_normal_chat_and_owner_commands_unaffected(self):
        c, ev = await self.boot()
        c.handlers[ev.MessageEv](c, self.event("hey, are you free tonight?", mid="N1"))
        c.handlers[ev.MessageEv](c, self.event("®ping", sender=OWNER, mid="N2"))
        await self.wait_for(lambda: c.replied)
        self.assertEqual(c.blocked, [])
        self.assertIn("PONG", c.replied[0][0])
