import json
import os
import types
import urllib.request
from unittest import mock

from tests import base
from tests.base import HTTP_CALLS, HTTP_QUEUE, Base


class FakeSession:
    number = "2348130300032"

    def __init__(self):
        self.sent = []

    async def send_text(self, chat, text, quoted=None):
        self.sent.append((chat, text, quoted))

    @property
    def last(self):
        if not self.sent:
            return None
        text = self.sent[-1][1]
        marker = "\n\n╰━━━━━━━━━━━━━━━━━━╯\nDeveloper: 𝙳𝙴𝚅 𝙰𝚄𝚂𝚃𝙸𝙽"
        return text.split(marker, 1)[0] if marker in text else text


def msg(text, sender="2348130300032", chat=None, name="", quoted=None):
    from xcore.whatsapp.context import Incoming

    return Incoming(chat=chat or f"{sender}@s.whatsapp.net", sender=f"{sender}@s.whatsapp.net", sender_number=sender,
                    text=text, push_name=name, quoted_participant=quoted, raw=object())


class PipelineTests(Base):
    async def send(self, text, **kw):
        from xcore.whatsapp.handler import handle_message

        s = FakeSession()
        await handle_message(s, msg(text, **kw))
        return s

    async def test_basic_and_prefix(self):
        s = await self.send("®ping")
        self.assertEqual(s.last, "🏓 PONG")
        self.assertIsNotNone(s.sent[0][2])  # replies quote the original
        self.assertEqual((await self.send("ping")).sent, [])  # no prefix
        self.assertEqual((await self.send(".")).sent, [])
        self.assertEqual((await self.send(".nosuchcmd")).sent, [])
        self.assertEqual((await self.send("®PING")).last, "🏓 PONG")  # case-insensitive
        self.assertIn("Chat:", (await self.send("®get-id")).last)

    async def test_xcore_prefixes_and_branding_without_bug_command(self):
        from xcore.whatsapp.handler import handle_message

        for prefix in ("®", "@"):
            session = await self.send(prefix + "ping")
            self.assertIn("🏓 PONG", session.last)
            self.assertIn("Developer: 𝙳𝙴𝚅 𝙰𝚄𝚂𝚃𝙸𝙽", session.sent[-1][1])

        session = await self.send("®botinfo")
        self.assertIn("╭━━━〔 𝚇𝙲𝙾𝚁𝙴® 〕━━━╮", session.last)

        session = await self.send("@bug 2348000000000 Hello target")
        self.assertEqual(session.sent, [])
        from xcore.whatsapp.commands import REGISTRY
        self.assertNotIn("bug", REGISTRY)

    async def test_apify_help_and_actor_list_do_not_require_token(self):
        from xcore.whatsapp.handler import handle_message
        from xcore import config

        os.environ.pop("APIFY_TOKEN", None)
        config.reset()
        help_session = await self.send("®apify help")
        self.assertIn("APIFY CONTROL", help_session.last)
        actors_session = await self.send("®apify actors")
        self.assertIn("groups:", actors_session.last)

    async def test_groups_reports_zero_after_invalid_links_are_filtered(self):
        from unittest.mock import patch
        from xcore.whatsapp.commands import apify as apify_commands

        fake_items = [
            {"name": "Not a WhatsApp link", "invite_url": "https://example.com/group"},
            {"name": "Bad scheme", "invite_url": "http://chat.whatsapp.com/invalid"},
        ]
        with patch.object(apify_commands, "_run_actor", return_value=fake_items):
            session = await self.send("®groups fashion NG 10")
        self.assertIn("No public WhatsApp invite links found", session.last)
        self.assertNotIn("Results: 1", session.last)

    async def test_modes(self):
        from xcore import db

        user = "2349000000001"
        db.set_mode("self")
        self.assertEqual((await self.send("®ping", sender=user)).last, "🏓 PONG")  # public-ok command
        self.assertIn("RESTRICTED ACCESS", (await self.send("®stats", sender=user)).last.upper())  # not public in self mode
        self.assertTrue((await self.send("®stats")).sent)  # owner ok
        db.add_sudo(user)
        self.assertTrue((await self.send("®stats", sender=user)).sent)  # sudo ok
        db.remove_sudo(user)
        db.set_mode("public")
        self.assertTrue((await self.send("®stats", sender=user)).sent)
        for m in ("dev", "stealth"):
            db.set_mode(m)
            self.assertEqual((await self.send("®ping", sender=user)).sent, [])  # stealth/dev stay silent
            self.assertTrue((await self.send("®ping")).sent)
        db.set_mode("maintenance")
        self.assertEqual((await self.send("®stats", sender=user)).sent, [])
        self.assertTrue((await self.send("®menu", sender=user)).sent)
        self.assertEqual((await self.send("®ping", sender=user)).sent, [])  # maintenance keeps non-allowlisted commands silent

    async def test_banned_removed_silent_but_owner_exempt(self):
        from xcore import db

        user = "2349000000002"
        db.ban_user(user, 0, "x")
        self.assertIn("RESTRICTED ACCESS", (await self.send("®ping", sender=user)).last.upper())
        db.unban_user(user)
        db.remove_user(user)
        self.assertIn("RESTRICTED ACCESS", (await self.send("®ping", sender=user)).last.upper())
        db.ban_user(self.OWNER, 0, "x")
        self.assertTrue((await self.send("®ping")).sent)

    async def test_owner_only_and_osint_lock(self):
        from xcore import db

        user = "2349000000003"
        self.assertEqual((await self.send("®ban 1 0", sender=user)).last, "❌ Owner-only command.")
        self.assertEqual((await self.send("®mode dev", sender=user)).last, "⛔ Owner only.")
        self.assertEqual((await self.send("®monitor list", sender=user)).last, "⛔ Sudo only.")
        self.assertIn("Premium-only", (await self.send("®wafind x", sender=user)).last)
        db.add_premium(user, 5)
        self.assertIn("disabled", (await self.send("®wafind x", sender=user)).last)
        self.assertEqual((await self.send("®self", sender=user)).last, "❌ Sudo-only command.")

    async def test_owner_admin_flow(self):
        from xcore import db

        u = "2349000000004"
        s = await self.send(f"®premium add {u} 10")
        self.assertEqual(s.last, "💎 Premium added.")
        self.assertTrue(db.is_premium(u))
        self.assertIn(f"+{u} • standard • ", (await self.send("®premium list")).last)
        self.assertEqual((await self.send("®premium add")).last, "❌ Usage: ®premium add <number> <days>")
        self.assertEqual((await self.send(f"®premium remove {u}")).last, "💎 Premium removed.")
        self.assertEqual((await self.send(f"®ban {u} 2 being rude")).last, "🚫 Local ban added to User.")
        self.assertEqual(db.list_banned()[0]["reason"], "being rude")
        self.assertIn("being rude", (await self.send("®banned")).last)
        self.assertEqual((await self.send(f"®unban {u}")).last, "✅ Local ban removed.")
        self.assertEqual((await self.send(f"®sudo {u}")).last, "⚔️ Sudo added.")
        self.assertTrue(db.is_sudo(u))
        self.assertEqual((await self.send(f"®delsudo {u}")).last, "⚔️ Sudo removed.")
        self.assertEqual((await self.send("®sudo")).last, "❌ Reply to a user or provide a number.")
        s = await self.send("®sudo", quoted=f"{u}:7@s.whatsapp.net")  # reply-to-user form
        self.assertTrue(db.is_sudo(u))
        self.assertEqual((await self.send(f"®remove {u}")).last, "⛔ User removed from local bot access.")
        self.assertEqual(db.get_role(u), "REMOVED")
        self.assertEqual((await self.send(f"®restore {u}")).last, "✅ User access restored.")
        self.assertIn("Current: *public*", (await self.send("®mode")).last)
        self.assertIn("Mode → *dev*", (await self.send("®mode dev")).last)
        self.assertIn("Unknown mode", (await self.send("®mode zzz")).last)
        self.assertEqual((await self.send("®antiban off")).last, "🛡 Antiban OFF.")
        self.assertEqual((await self.send("®antiban")).last, "🛡 Antiban: OFF")

    async def test_restart_exits_via_supervisor(self):
        with mock.patch("os._exit") as ex:
            s = await self.send("®restart")
            self.assertEqual(s.last, "♻️ Restarting…")
            import asyncio

            await asyncio.sleep(0.7)
            ex.assert_called_once_with(0)

    async def test_antibug_and_names(self):
        from xcore import db
        from xcore.whatsapp.handler import handle_message

        s = FakeSession()
        await handle_message(s, msg("A" * 20001, name="Alice"))
        self.assertEqual(db.list_bugs()[0]["reason"], "oversized payload")
        self.assertEqual(db.get_name("2348130300032@s.whatsapp.net"), "Alice")
        self.assertIn("oversized payload", (await self.send("®antibug")).last)

    async def test_command_errors_become_replies(self):
        s = await self.send("®bancheck")
        self.assertEqual(s.last, "❌ A phone number is required.")
        HTTP_QUEUE.append(RuntimeError("boom"))
        s = await self.send("®bancheck 2348000000000")
        self.assertEqual(s.last, "❌ boom")

    async def test_bancheck_card_and_stats(self):
        from xcore import db

        HTTP_QUEUE.append(base.sys.modules["httpx"].Response(200, body={"banned": True, "reason": "spam"}))
        s = await self.send("®bancheck +2348000000000")
        self.assertIn("🔴 BANNED", s.last)
        self.assertIn("Region: NG (+234)", s.last)
        self.assertIn("Reason: spam", s.last)
        self.assertEqual(db.get_stats()["banned"], 1)
        method, url, kw = HTTP_CALLS[-1]
        self.assertEqual((method, kw["json"]), ("POST", {"phone": "2348000000000"}))
        self.assertEqual(kw["headers"], {"Authorization": "Bearer k"})

    async def test_misc_info_commands(self):
        for cmd, frag in [("®uptime", "UPTIME"), ("®support", "+2348130300032"), ("®buypremium", "PREMIUM"),
                          ("®checkaccess", "Role: OWNER"), ("®device", "Python"), ("®session", "Active sessions: 0"),
                          ("®stats", "STATS"), ("®health", "Monitors:  0"), ("®api", "Method: POST"), ("®menu", "OWNER"),
                          ("®services", "No services monitored.")]:
            with self.subTest(cmd=cmd):
                self.assertIn(frag, (await self.send(cmd)).last)


class DevtoolTests(Base):
    async def send(self, text, **kw):
        from xcore.whatsapp.handler import handle_message

        s = FakeSession()
        await handle_message(s, msg(text, **kw))
        return s

    async def test_monitor_commands(self):
        from xcore import db

        with mock.patch("xcore.whatsapp.commands.devtools.assert_safe_url", mock.AsyncMock()):
            self.assertIn("Monitoring: https://x.test", (await self.send("®monitor https://x.test")).last)
            self.assertIn("Already monitoring", (await self.send("®monitor https://x.test")).last)
        self.assertIn("1. ⚪ https://x.test", (await self.send("®monitor list")).last)
        self.assertIn("Removed monitor", (await self.send("®monitor remove https://x.test")).last)
        self.assertEqual(db.list_monitors(), [])
        self.assertIn("Local/private", (await self.send("®monitor http://localhost")).last)

    async def test_api_test_command(self):
        R = base.sys.modules["httpx"].Response
        HTTP_QUEUE.append(R(200, body={"banned": False}))
        result = await self.send("®api-test")
        self.assertIn("𝚇𝙲𝙾𝚁𝙴® API TEST", result.last)
        self.assertIn("Status: REACHABLE", result.last)
        self.assertIn("Latency:", result.last)
        self.assertEqual(HTTP_CALLS[-1][2]["json"], {"phone": "0000000000"})
        HTTP_QUEUE.append(RuntimeError("provider unavailable"))
        failed = await self.send("®api-test")
        self.assertIn("Status: FAILED", failed.last)
        self.assertIn("provider unavailable", failed.last)

    async def test_apilab_and_watch(self):
        R = base.sys.modules["httpx"].Response
        HTTP_QUEUE.append(R(200, body={"a": 1}, headers={"content-type": "application/json", "server": "nginx"}))
        with mock.patch("xcore.whatsapp.commands.devtools.assert_safe_url", mock.AsyncMock()):
            s = await self.send("®apilab get https://x.test")
            self.assertIn("Status:  200 OK", s.last)
            self.assertIn("Server:  nginx", s.last)
            self.assertEqual(HTTP_CALLS[-1][0], "GET")
            HTTP_QUEUE.append(R(200, body={}))
            await self.send("®api headers https://x.test")  # `.api` delegates to apilab
            self.assertEqual(HTTP_CALLS[-1][0], "GET")
            HTTP_QUEUE.append(RuntimeError("timed out"))
            self.assertIn("Error: timed out", (await self.send("®watch https://x.test")).last)
            HTTP_QUEUE.append(R(204, body={}))
            self.assertIn("Status: 204", (await self.send("®watch https://x.test")).last)
        self.assertIn("Unsupported method: trace", (await self.send("®apilab trace https://x.test")).last)
        self.assertIn("API LABORATORY", (await self.send("®apilab")).last.replace("𝗔𝗣𝗜 𝗟𝗔𝗕𝗢𝗥𝗔𝗧𝗢𝗥𝗬", "API LABORATORY"))
        self.assertIn("Private/local IP", (await self.send("®apilab get http://10.0.0.1")).last)

    async def test_scan(self):
        from xcore import clans, db

        clans.add_clan({"name": "Raven Clan", "tag": "『𐀀』"})
        db.save_name("2349000000009@s.whatsapp.net", "Bob 『𐀀』")
        s = await self.send("®scan", quoted="2349000000009:3@s.whatsapp.net")
        self.assertIn("Clan name   : Raven Clan", s.last)
        self.assertIn("Confidence  : 98%", s.last)
        self.assertIn("NOT FOUND", (await self.send("®scan 2340000000000")).last)
        self.assertIn("Clan name   : Raven Clan", (await self.send("®scan 2349000000009")).last)
        self.assertIn("Raven Clan", (await self.send("®scan list")).last)
        self.assertIn("Added clan: Foo (⟁)", (await self.send("®scan add Foo | ⟁ | desc")).last)
        self.assertIn("Usage: ®scan add", (await self.send("®scan add onlyname")).last)
        self.assertIn("Usage:", (await self.send("®scan")).last)
        self.assertIn("Invalid number", (await self.send("®scan abc")).last)


class MonitorTests(Base):
    async def test_alerts_and_recovery_downtime(self):
        from xcore import db, monitor

        db.add_monitor("https://svc.test", "chat@g.us", "1")
        alerts = []

        async def notify(chat, text):
            alerts.append((chat, text))

        monitor.set_notifier(notify)
        seq = iter([{"up": True, "status": 200, "latency": 5}, {"up": False, "status": 0, "latency": 9},
                    {"up": False, "status": 500, "latency": 9}, {"up": True, "status": 200, "latency": 4}])

        async def fake_check(_m):
            return next(seq)

        with mock.patch.object(monitor, "check_one", fake_check):
            await monitor.tick()  # unknown → up : no alert
            self.assertEqual(alerts, [])
            await monitor.tick()  # up → down : alert
            self.assertEqual(len(alerts), 1)
            self.assertIn("STATUS: DOWN", alerts[0][1])
            since = db.list_monitors()[0]["downSince"]
            self.assertIsNotNone(since)
            await monitor.tick()  # still down: no repeat alert, downSince preserved
            self.assertEqual(len(alerts), 1)
            self.assertEqual(db.list_monitors()[0]["downSince"], since)
            db.list_monitors()[0]["downSince"] = db.now_ms() - 125_000  # pretend it's been down 2m05s
            await monitor.tick()  # down → up: recovery with real downtime
        self.assertEqual(len(alerts), 2)
        self.assertIn("Downtime: 2m 5s", alerts[1][1])
        m = db.list_monitors()[0]
        self.assertEqual((m["status"], m["totalChecks"], m["totalDown"], m["downSince"]), ("up", 4, 2, None))

    async def test_check_one_uses_guard(self):
        from xcore import monitor

        r = await monitor.check_one({"url": "http://127.0.0.1/"})
        self.assertFalse(r["up"])
        self.assertIn("Private/local", r["error"])
        base.HTTP_QUEUE.append(base.sys.modules["httpx"].Response(301, body={}))
        with mock.patch.object(monitor, "assert_safe_url", mock.AsyncMock()):
            self.assertTrue((await monitor.check_one({"url": "https://x"}))["up"])  # 3xx counts as up
            base.HTTP_QUEUE.append(base.sys.modules["httpx"].Response(503, body={}))
            self.assertFalse((await monitor.check_one({"url": "https://x"}))["up"])


class ApiTests(Base):
    async def test_status_interpretation(self):
        from xcore import api

        R = base.sys.modules["httpx"].Response
        cases = [({"banned": False}, False), ({"isBanned": "yes"}, True), ({"status": "Not Banned"}, False),
                 ({"status": "weird"}, None), ({}, None), ({"blocked": "true", "reason": "r"}, True)]
        for body, expected in cases:
            HTTP_QUEUE.append(R(200, body=body))
            self.assertEqual((await api.check_ban("123"))["banned"], expected, body)
        HTTP_QUEUE.append(R(403, body={}))
        with self.assertRaisesRegex(RuntimeError, "HTTP 403"):
            await api.check_ban("123")
        HTTP_QUEUE.append(R(200, body=None))  # non-JSON body
        self.assertIsNone((await api.check_ban("123"))["banned"])
        with self.assertRaises(ValueError):
            api.normalize_phone("abc")


class FormatTests(Base):
    def test_region_and_menu_by_role(self):
        from xcore import db, format as fmt

        self.assertEqual(fmt.region("2348000"), "NG (+234)")
        self.assertEqual(fmt.region("27821234567"), "ZA (+27)")
        self.assertEqual(fmt.region("14155550123"), "US (+1)")
        self.assertEqual(fmt.region("447700900123"), "UK (+44)")
        self.assertEqual(fmt.region("91999"), "Unknown")
        user = fmt.menu("2349000000010")
        self.assertIn("Buy premium", user)
        self.assertNotIn("®watch", user)
        self.assertNotIn("✦ 𝗢𝗪𝗡𝗘𝗥", user)
        db.add_premium("2349000000010", 5)
        self.assertIn("®wafind", fmt.menu("2349000000010"))
        owner = fmt.menu(self.OWNER)
        for frag in ("®restore", "®restart", "®scan (reply)", "MODE: PUBLIC".replace("MODE", "𝗠𝗢𝗗𝗘")):
            self.assertIn(frag, owner)
        db.ban_user("2349000000011", 3, "bad")
        card = fmt.access_card("2349000000011")
        self.assertIn("Role: BANNED", card)
        self.assertIn("Reason: bad", card)
        self.assertRegex(card, r"Until: [23] days")


class SocketParseTests(Base):
    def _ev(self, **kw):
        N = types.SimpleNamespace
        jid = lambda u, s, d=0: N(User=u, Server=s, Device=d)
        sender = kw.get("sender", jid("2349000000001", "s.whatsapp.net"))
        src = N(Chat=kw.get("chat", jid("120363@", "g.us")), Sender=sender, SenderAlt=kw.get("alt"), IsFromMe=False)
        ext = N(text=kw.get("ext", ""), contextInfo=N(participant=kw.get("quoted", "")))
        m = N(conversation=kw.get("conv", ""), extendedTextMessage=ext, imageMessage=N(caption=kw.get("cap", "")),
              videoMessage=N(caption=""))
        return N(Info=N(MessageSource=src, Pushname=kw.get("push", "")), Message=m)

    def test_parse(self):
        from xcore.whatsapp.socket import parse_message

        m = parse_message(self._ev(conv="®ping", push="Zed"))
        self.assertEqual((m.chat, m.text, m.sender_number, m.push_name), ("120363@@g.us", "®ping", "2349000000001", "Zed"))
        self.assertEqual(parse_message(self._ev(ext="®menu")).text, "®menu")
        self.assertEqual(parse_message(self._ev(cap="®scan")).text, "®scan")
        self.assertEqual(parse_message(self._ev(ext="®sudo", quoted="2348@s.whatsapp.net")).quoted_participant, "2348@s.whatsapp.net")
        self.assertIsNone(parse_message(self._ev(conv="x")).quoted_participant)
        N = types.SimpleNamespace
        lid = N(User="99887766", Server="lid", Device=0)
        alt = N(User="2349111111111", Server="s.whatsapp.net", Device=0)
        m = parse_message(self._ev(sender=lid, alt=alt, conv=".x"))
        self.assertEqual((m.sender_number, m.sender), ("2349111111111", "2349111111111@s.whatsapp.net"))
        self.assertIsNone(parse_message(self._ev(chat=N(User="status", Server="broadcast", Device=0))))
        self.assertIsNone(parse_message(N()))


class ServerTests(Base):
    def test_health_endpoint(self):
        from xcore import server

        srv = server.start(lambda: 2)
        try:
            port = srv.server_address[1]
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as r:
                body = json.load(r)
                self.assertEqual(r.headers["Cache-Control"], "no-store")
        finally:
            server.stop()
        self.assertEqual((body["status"], body["sessions"], body["mode"], body["name"]), ("online", 2, "public", "𝚇𝙲𝙾𝚁𝙴®"))
        self.assertTrue(body["ts"].endswith("Z"))
