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


if __name__ == "__main__":
    unittest.main()
