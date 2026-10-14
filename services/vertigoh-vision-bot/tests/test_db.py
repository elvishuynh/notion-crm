import unittest
import tempfile
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path

from db import ReviewDatabase, calculate_next_ping, to_iso, from_iso


class TestReviewDatabase(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = str(Path(self.test_dir) / "test.db")
        self.db = ReviewDatabase(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_calculate_next_ping(self):
        base = datetime(2026, 10, 12, 12, 0, 0, tzinfo=timezone.utc)
        # initial alert ping
        self.assertEqual(calculate_next_ping(0, base), base + timedelta(hours=12))
        # first reminder
        self.assertEqual(calculate_next_ping(1, base), base + timedelta(hours=8))
        # second reminder
        self.assertEqual(calculate_next_ping(2, base), base + timedelta(hours=4))
        # recurring pings
        self.assertEqual(calculate_next_ping(3, base), base + timedelta(hours=2))
        self.assertEqual(calculate_next_ping(4, base), base + timedelta(hours=2))

    def test_record_new_asset(self):
        t0 = datetime(2026, 10, 12, 10, 0, 0, tzinfo=timezone.utc)
        asset = self.db.record_new_asset(
            notion_page_id="page_123",
            page_title="Video Reel 1",
            asset_url="https://drive.google.com/asset1",
            discord_channel_id=111222,
            discord_message_id=999888,
            initial_time=t0,
        )
        self.assertEqual(asset["notion_page_id"], "page_123")
        self.assertEqual(asset["page_title"], "Video Reel 1")
        self.assertEqual(asset["status"], "PENDING")
        self.assertEqual(asset["stage"], 0)
        self.assertEqual(from_iso(asset["next_ping_at"]), t0 + timedelta(hours=12))

    def test_escalation_progression(self):
        t0 = datetime(2026, 10, 12, 0, 0, 0, tzinfo=timezone.utc)
        self.db.record_new_asset(
            notion_page_id="page_escalate",
            page_title="Reel 2",
            asset_url="https://drive.google.com/asset2",
            discord_channel_id=111,
            discord_message_id=222,
            initial_time=t0,
        )

        # not due yet
        t_6h = t0 + timedelta(hours=6)
        self.assertEqual(len(self.db.get_due_escalations(t_6h)), 0)

        # due for ping
        t_12h = t0 + timedelta(hours=12)
        due = self.db.get_due_escalations(t_12h)
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["stage"], 0)

        # advance to stage one
        updated = self.db.advance_escalation_stage("page_escalate", ping_time=t_12h)
        self.assertEqual(updated["stage"], 1)
        self.assertEqual(from_iso(updated["next_ping_at"]), t_12h + timedelta(hours=8))

        # still not due
        self.assertEqual(len(self.db.get_due_escalations(t0 + timedelta(hours=19))), 0)

        # due for stage two
        t_20h = t0 + timedelta(hours=20)
        due = self.db.get_due_escalations(t_20h)
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["stage"], 1)

        # advance to stage two
        updated = self.db.advance_escalation_stage("page_escalate", ping_time=t_20h)
        self.assertEqual(updated["stage"], 2)
        self.assertEqual(from_iso(updated["next_ping_at"]), t_20h + timedelta(hours=4))

        # due for stage three
        t_24h = t0 + timedelta(hours=24)
        due = self.db.get_due_escalations(t_24h)
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["stage"], 2)

        # advance to stage three
        updated = self.db.advance_escalation_stage("page_escalate", ping_time=t_24h)
        self.assertEqual(updated["stage"], 3)
        self.assertEqual(from_iso(updated["next_ping_at"]), t_24h + timedelta(hours=2))

        # recurring interval check
        t_26h = t0 + timedelta(hours=26)
        updated = self.db.advance_escalation_stage("page_escalate", ping_time=t_26h)
        self.assertEqual(updated["stage"], 4)
        self.assertEqual(from_iso(updated["next_ping_at"]), t_26h + timedelta(hours=2))

    def test_mark_reviewed(self):
        t0 = datetime(2026, 10, 12, 0, 0, 0, tzinfo=timezone.utc)
        self.db.record_new_asset(
            notion_page_id="page_rev",
            page_title="Reel 3",
            asset_url="https://drive.google.com/asset3",
            discord_channel_id=111,
            discord_message_id=333,
            initial_time=t0,
        )

        # mark as reviewed
        reviewed = self.db.mark_reviewed_by_message_id(333, reviewer_id="user_elvis")
        self.assertIsNotNone(reviewed)
        self.assertEqual(reviewed["status"], "REVIEWED")
        self.assertEqual(reviewed["reviewed_by"], "user_elvis")

        # verify no future pings
        t_future = t0 + timedelta(days=7)
        due = self.db.get_due_escalations(t_future)
        self.assertEqual(len(due), 0)

    def test_upsert_and_find_discord_user(self):
        self.db.upsert_discord_user(
            user_id="264314531818242049",
            username="kaivertigoh",
            display_name="Kai Goh",
            roles="Reviewer",
        )
        users = self.db.get_all_discord_users()
        self.assertEqual(len(users), 1)
        self.assertEqual(users[0]["username"], "kaivertigoh")

        # match by username
        found = self.db.find_user_by_name("kaivertigoh")
        self.assertIsNotNone(found)
        self.assertEqual(found["user_id"], "264314531818242049")

        # match handle with at
        found_at = self.db.find_user_by_name("@kaivertigoh")
        self.assertIsNotNone(found_at)
        self.assertEqual(found_at["user_id"], "264314531818242049")

        # match display name
        found_display = self.db.find_user_by_name("Kai Goh")
        self.assertIsNotNone(found_display)
        self.assertEqual(found_display["user_id"], "264314531818242049")

    def test_record_and_escalate_custom_reminder(self):
        t0 = datetime(2026, 10, 12, 0, 0, 0, tzinfo=timezone.utc)
        rem = self.db.record_custom_reminder(
            reminder_id="rem_1",
            message_text="Check the final cut export",
            discord_guild_id=1504290249717710919,
            discord_channel_id=1559386893362266162,
            discord_message_id=888111,
            target_user_id="264314531818242049",
            created_by_user_id="259869751130587137",
            initial_time=t0,
        )
        self.assertEqual(rem["reminder_id"], "rem_1")
        self.assertEqual(rem["status"], "PENDING")
        self.assertEqual(rem["stage"], 0)
        self.assertEqual(from_iso(rem["next_ping_at"]), t0 + timedelta(hours=12))

        # check due timestamp
        due = self.db.get_due_custom_reminders(t0 + timedelta(hours=12))
        self.assertEqual(len(due), 1)

        # advance reminder stage
        advanced = self.db.advance_custom_reminder_stage("rem_1", ping_time=t0 + timedelta(hours=12))
        self.assertEqual(advanced["stage"], 1)
        self.assertEqual(from_iso(advanced["next_ping_at"]), t0 + timedelta(hours=20))

        # mark acknowledged
        acked = self.db.mark_custom_reminder_acknowledged(888111, acknowledged_by="264314531818242049")
        self.assertIsNotNone(acked)
        self.assertEqual(acked["status"], "REVIEWED")
        self.assertEqual(acked["acknowledged_by"], "264314531818242049")

        # verify removed from queue
        self.assertEqual(len(self.db.get_due_custom_reminders(t0 + timedelta(days=5))), 0)

    def test_calculate_edit_next_ping(self):
        from db import calculate_edit_next_ping
        base = datetime(2026, 10, 12, 12, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(calculate_edit_next_ping(0, base), base + timedelta(hours=24))
        self.assertEqual(calculate_edit_next_ping(1, base), base + timedelta(hours=24))

    def test_snooze(self):
        self.assertIsNone(self.db.get_snooze_until())
        until = datetime.now(timezone.utc) + timedelta(hours=12)
        self.db.set_snooze(until)
        active = self.db.get_snooze_until()
        self.assertIsNotNone(active)

        self.db.clear_snooze()
        self.assertIsNone(self.db.get_snooze_until())

    def test_user_snooze(self):
        # test user individual snooze
        self.assertFalse(self.db.is_user_snoozed("user_1"))
        until = datetime.now(timezone.utc) + timedelta(hours=12)
        self.db.set_user_snooze("user_1", until)
        self.assertTrue(self.db.is_user_snoozed("user_1"))
        self.assertFalse(self.db.is_user_snoozed("user_2"))

        active = self.db.get_user_snooze("user_1")
        self.assertIsNotNone(active)

        self.db.clear_user_snooze("user_1")
        self.assertFalse(self.db.is_user_snoozed("user_1"))

        # expired snooze returns false
        expired = datetime.now(timezone.utc) - timedelta(hours=1)
        self.db.set_user_snooze("user_3", expired)
        self.assertFalse(self.db.is_user_snoozed("user_3"))

    def test_notion_item_lifecycle(self):
        t0 = datetime(2026, 10, 12, 0, 0, 0, tzinfo=timezone.utc)
        item = self.db.record_notion_item(
            notion_page_id="page_p1",
            category="REVIEW",
            page_title="Review Clip",
            asset_url="https://vimeo.com/p1",
            notion_status="Review Pending",
            assigned_users="Roger,Kai",
            discord_channel_id=111,
            discord_message_id=222,
            initial_time=t0,
        )
        self.assertEqual(item["tracking_id"], "page_p1:REVIEW")
        self.assertEqual(item["category"], "REVIEW")
        self.assertEqual(item["stage"], 0)
        self.assertEqual(from_iso(item["next_ping_at"]), t0 + timedelta(hours=12))

        # advance
        adv = self.db.advance_notion_item_stage("page_p1:REVIEW", ping_time=t0 + timedelta(hours=12))
        self.assertEqual(adv["stage"], 1)
        self.assertEqual(from_iso(adv["next_ping_at"]), t0 + timedelta(hours=20))

        # resolve
        res = self.db.mark_notion_item_resolved("page_p1:REVIEW")
        self.assertEqual(res["status"], "RESOLVED")
        self.assertEqual(len(self.db.get_active_notion_items("REVIEW")), 0)

    def test_notion_item_post_lifecycle(self):
        # test post lifecycle 24h
        t0 = datetime(2026, 10, 12, 0, 0, 0, tzinfo=timezone.utc)
        item = self.db.record_notion_item(
            notion_page_id="page_post_db",
            category="POST",
            page_title="Ready Clip",
            asset_url="https://vimeo.com/post_db",
            notion_status="Ready to Post",
            assigned_users="Elvis",
            discord_channel_id=111,
            discord_message_id=444,
            initial_time=t0,
        )
        self.assertEqual(item["tracking_id"], "page_post_db:POST")
        self.assertEqual(item["category"], "POST")
        self.assertEqual(item["stage"], 0)
        self.assertEqual(from_iso(item["next_ping_at"]), t0 + timedelta(hours=24))

        # advance stage 24h
        adv = self.db.advance_notion_item_stage("page_post_db:POST", ping_time=t0 + timedelta(hours=24))
        self.assertEqual(adv["stage"], 1)
        self.assertEqual(from_iso(adv["next_ping_at"]), t0 + timedelta(hours=48))

        active_posts = self.db.get_active_notion_items("POST")
        self.assertEqual(len(active_posts), 1)

        res = self.db.mark_notion_item_resolved("page_post_db:POST")
        self.assertEqual(res["status"], "RESOLVED")
        self.assertEqual(len(self.db.get_active_notion_items("POST")), 0)

    def test_custom_reminder_pings_and_lookup(self):
        # test ping tracking
        t0 = datetime(2026, 10, 12, 0, 0, 0, tzinfo=timezone.utc)
        self.db.record_custom_reminder(
            reminder_id="rem_ping_test",
            message_text="Sleep on time",
            discord_guild_id=123,
            discord_channel_id=456,
            discord_message_id=789,
            target_user_id="user_elvis",
            created_by_user_id="user_roger",
            initial_time=t0,
        )

        self.db.record_custom_reminder_ping(
            ping_message_id=999111,
            reminder_id="rem_ping_test",
            discord_message_id=789,
            ping_time=t0 + timedelta(hours=12),
        )

        # verify ping lookup
        pings = self.db.get_custom_reminder_pings("rem_ping_test")
        self.assertEqual(len(pings), 1)
        self.assertEqual(pings[0]["ping_message_id"], 999111)

        # lookup by original
        found_orig = self.db.get_custom_reminder_by_any_message_id(789)
        self.assertIsNotNone(found_orig)
        self.assertEqual(found_orig["reminder_id"], "rem_ping_test")

        # lookup by ping
        found_ping = self.db.get_custom_reminder_by_any_message_id(999111)
        self.assertIsNotNone(found_ping)
        self.assertEqual(found_ping["reminder_id"], "rem_ping_test")

        # ack via ping id
        acked = self.db.mark_custom_reminder_acknowledged(999111, acknowledged_by="user_elvis")
        self.assertIsNotNone(acked)
        self.assertEqual(acked["status"], "REVIEWED")
        self.assertEqual(acked["acknowledged_by"], "user_elvis")

    def test_get_all_pending_custom_reminders(self):
        # test pending reminder listing
        self.db.record_custom_reminder(
            reminder_id="rem_a",
            message_text="Task A",
            discord_guild_id=1,
            discord_channel_id=2,
            discord_message_id=3,
            target_user_id="u1",
            created_by_user_id="u2",
        )
        self.db.record_custom_reminder(
            reminder_id="rem_b",
            message_text="Task B",
            discord_guild_id=1,
            discord_channel_id=2,
            discord_message_id=4,
            target_user_id="u1",
            created_by_user_id="u2",
        )
        pending = self.db.get_all_pending_custom_reminders()
        self.assertEqual(len(pending), 2)

        self.db.mark_custom_reminder_acknowledged(3, acknowledged_by="u1")
        pending_after = self.db.get_all_pending_custom_reminders()
        self.assertEqual(len(pending_after), 1)
        self.assertEqual(pending_after[0]["reminder_id"], "rem_b")


if __name__ == "__main__":
    unittest.main()
