import unittest


class FinalUtilitiesRegistrationTests(unittest.TestCase):
    def test_requested_commands_are_registered(self):
        from xcore.whatsapp.commands import REGISTRY
        for name in ("gitclone", "mediafire", "save", "readqr", "expel"):
            self.assertIn(name, REGISTRY)

    def test_rich_menu_and_restricted_card(self):
        from xcore.format import menu, restricted_card
        import os
        os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
        os.environ.setdefault("BAN_CHECKER_API_KEY", "test-key")
        from xcore import config
        config._cfg = None
        number = config.get().owner.number
        rendered = menu(number)
        for feature in ("®gitclone", "®mediafire", "®save contact", "®readqr", "®expel", "®apify"):
            self.assertIn(feature, rendered)
        self.assertIn("RESTRICTED ACCESS", restricted_card().upper())
        self.assertIn("𝚇𝙲𝙾𝚁𝙴®", rendered)

    def test_mediafire_size_limit(self):
        from xcore.whatsapp.commands.utilities import MAX_MEDIA_BYTES
        self.assertEqual(MAX_MEDIA_BYTES, 50 * 1024 * 1024)
