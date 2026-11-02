import unittest
from pathlib import Path

from xcore.whatsapp.commands import REGISTRY


class XcoreBrandingTests(unittest.TestCase):
    def test_xcore_command_registered_and_legacy_menu_preserved(self):
        self.assertIn("xcore", REGISTRY)
        self.assertIn("menu", REGISTRY)

    def test_brand_assets_are_bundled(self):
        assets = Path(__file__).resolve().parents[1] / "xcore" / "assets"
        self.assertTrue((assets / "xcore-brand.png").is_file())
        self.assertTrue((assets / "xcore-id.mp3").is_file())


if __name__ == "__main__":
    unittest.main()
