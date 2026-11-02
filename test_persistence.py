"""Persistent storage across redeploys (Railway volume etc.)."""
import json
import os
from pathlib import Path

from tests.base import Base


def _fresh_process():
    """Forget everything held in memory, like a brand-new container start."""
    from xcore import clans, config, db

    config.reset()
    db.reset_cache()
    clans._cache = None


class PathResolutionTests(Base):
    def _no_explicit_dirs(self):
        os.environ.pop("DATA_DIR", None)
        os.environ.pop("SESSION_DIR", None)

    def test_volume_mount_is_used_for_data_and_sessions(self):
        from xcore import paths

        self._no_explicit_dirs()
        vol = self.tmp / "vol"
        os.environ["RAILWAY_VOLUME_MOUNT_PATH"] = str(vol)
        self.assertEqual(paths.data_dir(), vol.resolve() / "data")
        self.assertEqual(paths.session_dir(), vol.resolve() / "sessions")

    def test_explicit_dirs_beat_the_volume(self):
        from xcore import paths

        os.environ["RAILWAY_VOLUME_MOUNT_PATH"] = str(self.tmp / "vol")
        self.assertEqual(paths.data_dir(), (self.tmp / "data").resolve())

    def test_persist_dir_override(self):
        from xcore import paths

        self._no_explicit_dirs()
        os.environ["PERSIST_DIR"] = str(self.tmp / "p")
        self.assertEqual(paths.data_dir(), (self.tmp / "p").resolve() / "data")

    def test_warns_on_railway_without_volume(self):
        from xcore import paths

        self._no_explicit_dirs()
        os.environ["RAILWAY_ENVIRONMENT"] = "production"
        st = paths.status(self.tmp / "d", self.tmp / "s")
        self.assertFalse(st["persistent"])
        self.assertIn("volume", st["warnings"][0].lower())

    def test_ok_on_railway_with_volume(self):
        from xcore import paths

        self._no_explicit_dirs()
        vol = self.tmp / "vol"
        os.environ.update(RAILWAY_ENVIRONMENT="production", RAILWAY_VOLUME_MOUNT_PATH=str(vol))
        st = paths.bootstrap()
        self.assertTrue(st["persistent"], st["warnings"])
        self.assertTrue((vol / "data").is_dir() and (vol / "sessions").is_dir())

    def test_warns_when_dirs_are_outside_the_volume(self):
        from xcore import paths

        os.environ.update(RAILWAY_ENVIRONMENT="production", RAILWAY_VOLUME_MOUNT_PATH=str(self.tmp / "vol"))
        st = paths.status()  # DATA_DIR/SESSION_DIR (set by Base) point outside the volume
        self.assertFalse(st["persistent"])


class SeedTests(Base):
    def test_bootstrap_seeds_clans_once_and_never_overwrites(self):
        from xcore import clans, config, paths

        data = config.get().paths.data
        st = paths.bootstrap(data, config.get().paths.sessions)
        self.assertEqual(st["seeded"], ["clans.json"])
        seeded = json.loads((data / "clans.json").read_text(encoding="utf-8"))
        self.assertTrue(seeded["clans"])
        (data / "clans.json").write_text(json.dumps({"clans": [{"name": "Mine", "tag": "M"}]}), encoding="utf-8")
        self.assertEqual(paths.bootstrap(data, config.get().paths.sessions)["seeded"], [])
        clans._cache = None
        self.assertEqual([c["name"] for c in clans.list_clans()], ["Mine"])  # owner edits survive


class RedeployTests(Base):
    def test_everything_survives_a_redeploy(self):
        from xcore import clans, config, db, paths

        vol = self.tmp / "vol"
        os.environ.pop("DATA_DIR"), os.environ.pop("SESSION_DIR")
        os.environ["RAILWAY_VOLUME_MOUNT_PATH"] = str(vol)

        # ── deploy #1 ──
        _fresh_process()
        paths.bootstrap(config.get().paths.data, config.get().paths.sessions)
        db.add_sudo("111")
        db.add_premium("222", 0)
        db.ban_user("333", 0, "spam")
        db.set_mode("stealth")
        clans.add_clan({"name": "Raven", "tag": "RVN"})
        sess_file = config.get().paths.sessions / "2348130300032" / "session.sqlite3"
        sess_file.parent.mkdir(parents=True)
        sess_file.write_bytes(b"whatsapp-keys")

        # ── container replaced: only the volume remains ──
        _fresh_process()
        self.assertEqual(config.get().paths.data, vol.resolve() / "data")
        paths.bootstrap(config.get().paths.data, config.get().paths.sessions)
        self.assertEqual(db.list_sudo(), ["111"])
        self.assertTrue(db.is_premium("222"))
        self.assertTrue(db.is_banned("333"))
        self.assertEqual(db.get_mode(), "stealth")
        self.assertIn("RVN", [c["tag"] for c in clans.list_clans()])
        self.assertEqual(sess_file.read_bytes(), b"whatsapp-keys")  # no re-pairing needed


class DbResilienceTests(Base):
    def test_corrupt_db_is_quarantined_and_restored_from_backup(self):
        from xcore import config, db

        db.add_sudo("111")
        db.add_sudo("222")  # second save leaves a .bak holding the first state
        f = config.get().paths.db_file
        self.assertTrue(f.with_name("db.json.bak").exists())
        f.write_text("{ not json", encoding="utf-8")
        db.reset_cache()
        self.assertEqual(db.list_sudo(), ["111"])  # restored from .bak
        self.assertTrue(list(f.parent.glob("db.json.corrupt-*")))  # bad file kept, not overwritten

    def test_corrupt_db_without_backup_starts_empty_but_keeps_the_file(self):
        from xcore import config, db

        f = config.get().paths.db_file
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("garbage", encoding="utf-8")
        db.reset_cache()
        self.assertEqual(db.list_sudo(), [])
        self.assertTrue(list(f.parent.glob("db.json.corrupt-*")))

    def test_no_temp_files_left_behind(self):
        from xcore import config, db

        db.add_sudo("1")
        self.assertFalse(list(config.get().paths.data.glob("*.tmp")))


class DotenvIsolationTests(Base):
    def test_a_real_dotenv_in_cwd_does_not_leak_into_tests(self):
        from xcore import config

        cwd = os.getcwd()
        os.chdir(self.tmp)
        try:
            (self.tmp / ".env").write_text("FORCE_JOIN_URL_1=https://t.me/leak\nCOMMAND_PREFIX=!\n")
            config.reset()
            self.assertEqual(config.get().prefix, "®")
            self.assertEqual(config.get().telegram.force_join_urls, ())
        finally:
            os.chdir(cwd)
