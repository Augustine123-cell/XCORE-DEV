from tests.base import Base


class FakeGroupSession:
    number = "2348130300032"

    def __init__(self, members):
        self.sent = []
        self.members = members
        self.actions = []
        self.deleted = []

    async def send_text(self, chat, text, quoted=None):
        self.sent.append((chat, text, quoted))

    async def group_info(self, chat):
        return {"Participants": self.members}

    async def group_participants(self, chat, numbers, action):
        self.actions.append((chat, numbers, action))

    async def delete_message(self, chat, sender, message_id):
        self.deleted.append((chat, sender, message_id))


class XCoreGroupCommandTests(Base):
    async def _run(self, text, session, sender=None, chat="120363000000000000@g.us"):
        from xcore.whatsapp.context import Incoming
        from xcore.whatsapp.handler import handle_message
        from xcore import config
        number = sender or config.get().owner.number
        msg = Incoming(chat=chat, sender=f"{number}@s.whatsapp.net", sender_number=number,
                       text=text, push_name="Tester", message_id="cmd-1")
        await handle_message(session, msg)
        return session.sent[-1][1] if session.sent else ""

    def members(self):
        return [
            {"JID": "2348130300032@s.whatsapp.net", "IsAdmin": True},
            {"JID": "2348000000001@s.whatsapp.net", "IsAdmin": False},
            {"JID": "2348000000002@s.whatsapp.net", "IsAdmin": True},
            {"JID": "2348000000003@s.whatsapp.net", "IsSuperAdmin": True},
        ]

    async def test_ordain_strip_and_ban_use_group_api(self):
        session = FakeGroupSession(self.members())
        out = await self._run("®ordain 2348000000001", session)
        self.assertIn("promoted", out)
        self.assertEqual(session.actions[-1][2], "promote")
        out = await self._run("@strip 2348000000002", session)
        self.assertIn("privileges removed", out)
        self.assertEqual(session.actions[-1][2], "demote")
        out = await self._run("®ban 2348000000001", session)
        self.assertIn("Removed", out)
        self.assertEqual(session.actions[-1][2], "remove")

    async def test_purge_owner_only_and_sweepclean_confirmation(self):
        session = FakeGroupSession(self.members())
        out = await self._run("®purge", session)
        self.assertIn("Demoted: 1", out)
        self.assertEqual(session.actions[-1][1], ["2348000000002"])
        self.assertEqual(session.actions[-1][2], "demote")

        session.actions.clear()
        out = await self._run("®sweepclean", session)
        self.assertIn("Eligible members to remove: 1", out)
        self.assertEqual(session.actions, [])
        out = await self._run("®sweepclean confirm", session)
        self.assertIn("Removed: 1", out)
        self.assertEqual(session.actions[-1][1], ["2348000000001"])

    async def test_group_jid(self):
        session = FakeGroupSession(self.members())
        out = await self._run("@jid", session)
        self.assertIn("120363000000000000@g.us", out)


class AntibugTests(Base):
    async def test_seventh_message_triggers_soft_mute_and_deletes(self):
        from xcore.whatsapp.context import Incoming
        from xcore.whatsapp.security import reset_runtime_state, inspect_message
        from xcore import db
        reset_runtime_state()
        session = FakeGroupSession([{"JID": "2348130300032@s.whatsapp.net", "IsAdmin": True}, {"JID": "2348000000009@s.whatsapp.net", "IsAdmin": False}])
        for i in range(7):
            msg = Incoming(chat="120363000000000000@g.us", sender="2348000000009@s.whatsapp.net",
                           sender_number="2348000000009", text="spam repeated content", message_id=f"m{i}",
                           push_name="Spammer")
            await inspect_message(session, msg)
        self.assertEqual(len(session.deleted), 7)
        self.assertIsNotNone(db.get_mute("120363000000000000@g.us", "2348000000009"))
        msg = Incoming(chat="120363000000000000@g.us", sender="2348000000009@s.whatsapp.net",
                       sender_number="2348000000009", text="still here", message_id="m8")
        await inspect_message(session, msg)
        self.assertEqual(len(session.deleted), 8)

class TelegramPremiumPersistenceTests(Base):
    def test_payment_before_pairing_activates_after_pairing(self):
        from xcore import db
        self.assertIsNone(db.grant_telegram_premium(555001, 30))
        self.assertFalse(db.is_premium("2348000000008"))
        db.link_telegram_user(555001, "2348000000008")
        self.assertTrue(db.is_premium("2348000000008"))
        self.assertEqual(db.telegram_linked_number(555001), "2348000000008")

    def test_payment_after_pairing_activates_immediately(self):
        from xcore import db
        db.link_telegram_user(555002, "2348000000007")
        self.assertEqual(db.grant_telegram_premium(555002, 30), "2348000000007")
        self.assertTrue(db.is_premium("2348000000007"))

    def test_telegram_payment_charge_is_idempotent(self):
        from xcore import db
        db.link_telegram_user(555003, "2348000000006")
        first, phone = db.apply_telegram_payment("charge-abc", 555003, 30)
        second, phone2 = db.apply_telegram_payment("charge-abc", 555003, 30)
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(phone, "2348000000006")
        self.assertEqual(phone2, "2348000000006")
        self.assertEqual(len(db.list_premium()), 1)


class XCoreBrandingAndDashboardDataTests(Base):
    def test_whatsapp_branding_wraps_unbranded_text_once(self):
        from xcore.format import brand_message
        text = brand_message("A test response")
        self.assertTrue(text.startswith("╭━━━〔 𝚇𝙲𝙾𝚁𝙴® 〕━━━╮"))
        self.assertEqual(text.count("Developer: 𝙳𝙴𝚅 𝙰𝚄𝚂𝚃𝙸𝙽"), 1)

    def test_admin_can_link_telegram_and_whatsapp_when_granting_premium(self):
        from xcore import db
        db.grant_premium_identity(998877, "2348000000099", 45, by="test-owner")
        self.assertEqual(db.telegram_linked_number(998877), "2348000000099")
        self.assertTrue(db.is_premium("2348000000099"))

    def test_dashboard_can_list_telegram_links(self):
        from xcore import db
        db.link_telegram_user(998878, "2348000000098")
        self.assertIn({"telegram_id": 998878, "number": "2348000000098"}, db.list_telegram_links())

    def test_unpair_removes_telegram_link(self):
        from xcore import db
        db.link_telegram_user(998879, "2348000000097")
        db.unlink_telegram_number("2348000000097")
        self.assertIsNone(db.telegram_linked_number(998879))
