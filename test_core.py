import asyncio
import json
from unittest import mock

from tests.base import Base


class DbTests(Base):
    def test_roles_and_expiry(self):
        from xcore import db

        self.assertEqual(db.get_role(self.OWNER), "OWNER")
        self.assertEqual(db.get_role("111"), "USER")
        db.add_sudo("+1 (11)")
        self.assertEqual(db.get_role("111"), "SUDO")
        db.remove_sudo("111")
        db.add_premium("222", 30)
        self.assertEqual(db.get_role("222"), "PREMIUM")
        db.add_premium("333", 1)
        db.load()["premium"][-1]["until"] = db.now_ms() - 1
        self.assertFalse(db.is_premium("333"))
        db.ban_user("444", 1, "spam")
        self.assertEqual(db.get_role("444"), "BANNED")
        db.load()["banned"][0]["until"] = db.now_ms() - 1  # temp ban lapses and self-cleans
        self.assertFalse(db.is_banned("444"))
        self.assertEqual(db.load()["banned"], [])
        db.remove_user("222")
        self.assertEqual(db.get_role("222"), "REMOVED")
        db.restore_user("222")
        self.assertEqual(db.get_role("222"), "USER")
        db.add_paired("555")
        self.assertEqual(db.get_role("555"), "PAIRED")

    def test_persistence_and_node_schema(self):
        from xcore import config, db

        db.add_sudo("999")
        db.add_premium("888", 0)  # 0 days = permanent
        db.bump_stats(True)
        db.bump_stats(False)
        db.bump_stats(None)
        db.reset_cache()
        self.assertEqual(db.list_sudo(), ["999"])
        self.assertIsNone(db.load()["premium"][0]["until"])
        self.assertEqual(db.get_stats(), {"checks": 3, "banned": 1, "safe": 1})
        self.assertFalse(list(config.get().paths.data.glob("*.tmp")))
        # a db.json written by the Node bot (partial keys) loads cleanly
        config.get().paths.db_file.write_text(json.dumps({"sudo": ["1"], "mode": "dev", "stats": {"checks": 5}}))
        db.reset_cache()
        self.assertEqual(db.get_mode(), "dev")
        self.assertEqual(db.get_stats(), {"checks": 5, "banned": 0, "safe": 0})
        self.assertEqual(db.list_monitors(), [])

    def test_mode_validation_and_monitors_names(self):
        from xcore import db

        with self.assertRaises(ValueError):
            db.set_mode("nope")
        db.set_mode("stealth")
        self.assertEqual(db.get_mode(), "stealth")
        self.assertTrue(db.add_monitor("https://a.test", "c@g.us", "1"))
        self.assertFalse(db.add_monitor("https://a.test", "c@g.us", "1"))
        db.remove_monitor("https://a.test")
        self.assertEqual(db.list_monitors(), [])
        for i in range(2001):
            db.load()["names"][f"{i}@x"] = {"name": "n", "at": i}
        db.save_name("new@x", "New")
        self.assertEqual(len(db.load()["names"]), 2002 - 500)
        self.assertEqual(db.get_name("new@x"), "New")
        for i in range(250):
            db.log_bug({"sender": str(i)})
        self.assertEqual(len(db.load()["buglog"]), 200)
        self.assertEqual(db.list_bugs(3)[0]["sender"], "249")


class ClanTests(Base):
    def test_match_normalisation_add_remove(self):
        from xcore import clans

        clans.add_clan({"name": "Raven", "tag": "『𐀀』", "members": None})
        self.assertEqual(clans.list_clans()[0]["members"], 0)
        hits = clans.match_name("x​ 『𐀀』 Bob")
        self.assertEqual(hits[0]["name"], "Raven")
        self.assertEqual(clans.confidence(hits), 98)
        self.assertEqual(clans.match_name("nobody"), [])
        self.assertEqual(clans.confidence([]), 0)
        # NFKC: fancy-font tag matches plain text
        clans.add_clan({"name": "Fallen", "tag": "𝕱allen"})
        self.assertTrue(clans.match_name("Fallen One"))
        with self.assertRaises(ValueError):
            clans.add_clan({"name": "x"})
        clans.remove_clan("𝕱allen")
        self.assertFalse(clans.match_name("Fallen One"))

    def test_bundled_seed_file_is_valid(self):
        from tests.base import ROOT

        data = json.loads((ROOT / "xcore" / "seed" / "clans.json").read_text(encoding="utf-8"))
        self.assertTrue(data["clans"] and all("tag" in c for c in data["clans"]))


class UrlGuardTests(Base):
    async def test_blocks_and_allows(self):
        from xcore.urlguard import UrlBlocked, assert_safe_url

        bad = {
            "ftp://example.com": "Only http",
            "notaurl": "Invalid URL",
            "http://": "Invalid URL",
            "http://user:pw@example.com": "credentials",
            "http://localhost:8080": "Local/private",
            "http://foo.local": "Local/private",
            "http://127.0.0.1": "Private/local IP",
            "http://10.1.2.3": "Private/local IP",
            "http://169.254.169.254/latest": "Private/local IP",
            "http://192.168.0.1": "Private/local IP",
            "http://172.20.0.1": "Private/local IP",
            "http://100.64.0.1": "Private/local IP",
            "http://[::1]/": "Private/local IP",
            "http://[::ffff:127.0.0.1]/": "Private/local IP",
            "http://[fd00::1]/": "Private/local IP",
            "http://example.com:99999": "Invalid URL",
        }
        for url, frag in bad.items():
            with self.subTest(url=url), self.assertRaises(UrlBlocked) as cm:
                await assert_safe_url(url)
            self.assertIn(frag, str(cm.exception))
        self.assertEqual((await assert_safe_url("https://8.8.8.8/x")).hostname, "8.8.8.8")

        loop = asyncio.get_running_loop()
        pub = [(2, 1, 6, "", ("93.184.216.34", 0))]
        priv = [(2, 1, 6, "", ("93.184.216.34", 0)), (2, 1, 6, "", ("10.0.0.5", 0))]
        with mock.patch.object(loop, "getaddrinfo", mock.AsyncMock(return_value=pub)):
            await assert_safe_url("https://example.com")
        with mock.patch.object(loop, "getaddrinfo", mock.AsyncMock(return_value=priv)):
            with self.assertRaises(UrlBlocked) as cm:
                await assert_safe_url("https://rebind.example")
            self.assertIn("resolves to a private", str(cm.exception))
        with mock.patch.object(loop, "getaddrinfo", mock.AsyncMock(side_effect=OSError)):
            with self.assertRaises(UrlBlocked) as cm:
                await assert_safe_url("https://nx.example")
            self.assertIn("could not be resolved", str(cm.exception))


class ConfigTests(Base):
    def test_required_and_defaults(self):
        import os

        from xcore import config

        cfg = config.get()
        self.assertEqual(cfg.prefix, "®")
        self.assertEqual(cfg.api.method, "POST")
        self.assertEqual(cfg.api.timeout, 20.0)
        config.reset()
        os.environ["TELEGRAM_BOT_TOKEN"] = "PASTE_HERE"
        with self.assertRaises(config.ConfigError):
            config.get()
        os.environ["TELEGRAM_BOT_TOKEN"] = "x"
        os.environ["OWNER_NUMBER"] = "+234 813-030-0032"
        config.reset()
        self.assertEqual(config.get().owner.number, "2348130300032")
        os.environ["OWNER_NUMBERS"] = "2348130300032,+447700900123"
        os.environ["OWNER_TELEGRAM_IDS"] = "7895316756,123456"
        os.environ["FORCE_JOIN_URL_1"] = "https://t.me/shadowborne_news"
        os.environ["FORCE_JOIN_URL_2"] = ""
        config.reset()
        cfg = config.get()
        self.assertEqual(cfg.owner.numbers, ("2348130300032", "447700900123"))
        self.assertEqual(cfg.telegram.owner_ids, (7895316756, 123456))
        self.assertEqual(cfg.telegram.force_join_urls, ("https://t.me/shadowborne_news",))
        os.environ.pop("OWNER_NUMBERS", None)
        os.environ.pop("OWNER_TELEGRAM_IDS", None)
        os.environ.pop("FORCE_JOIN_URL_1", None)
        os.environ.pop("FORCE_JOIN_URL_2", None)
        config.reset()
    def test_force_join_target_parsing(self):
        from xcore.telegram_bot import _target_from_url

        self.assertEqual(_target_from_url("https://t.me/shadowborne_news"), "@shadowborne_news")
        self.assertEqual(_target_from_url("@shadowborne_news"), "@shadowborne_news")
        self.assertEqual(_target_from_url("-1001234567890"), "-1001234567890")
        self.assertIsNone(_target_from_url("https://t.me/+privateInvite"))
        self.assertIsNone(_target_from_url("https://example.com/not-telegram"))

