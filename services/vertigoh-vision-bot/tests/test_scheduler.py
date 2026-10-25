import unittest
from unittest.mock import AsyncMock, MagicMock
import tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta

from db import ReviewDatabase, from_iso
from scheduler import ReviewScheduler


class TestReviewScheduler(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test_scheduler.db")
        self.db = ReviewDatabase(self.db_path)

        self.mock_bot = MagicMock()
        self.mock_bot.is_target_snoozed = MagicMock(return_value=False)
        self.mock_bot.get_channel = MagicMock(return_value=None)
        self.mock_bot.send_review_alert = AsyncMock()
        self.mock_bot.send_edit_alert = AsyncMock()
        self.mock_bot.send_ready_to_post_alert = AsyncMock()
        self.mock_bot.send_notion_escalation = AsyncMock()
        self.mock_bot.update_resolved_message = AsyncMock()
        self.mock_bot.send_custom_escalation_ping = AsyncMock()

        self.mock_notion = MagicMock()
        self.mock_notion.query_assets_database = MagicMock()

        self.scheduler = ReviewScheduler(
            bot=self.mock_bot,
            notion=self.mock_notion,
            db=self.db,
            channel_id=987654,
            reviewer_id="reviewer_123",
            notion_interval=60,
            escalation_interval=30,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    async def test_poll_notion_review_pending(self):
        self.mock_notion.query_assets_database.return_value = [
            {
                "notion_page_id": "page_1",
                "page_title": "Promo Clip A",
                "asset_url": "https://vimeo.com/9991",
                "page_url": "https://notion.so/page_1",
                "status": "Review Pending",
                "reviewers": ["Roger"],
                "editors": [],
            }
        ]
        mock_msg = MagicMock()
        mock_msg.id = 555001
        self.mock_bot.send_review_alert.return_value = mock_msg

        await self.scheduler.poll_notion_once()

        self.mock_bot.send_review_alert.assert_called_once_with(
            channel_id=987654,
            page_title="Promo Clip A",
            asset_url="https://vimeo.com/9991",
            page_url="https://notion.so/page_1",
            reviewers=["Roger"],
        )

        item = self.db.get_notion_item("page_1:REVIEW")
        self.assertIsNotNone(item)
        self.assertEqual(item["discord_message_id"], 555001)
        self.assertEqual(item["status"], "PENDING")
        self.assertEqual(item["category"], "REVIEW")
        self.assertEqual(item["stage"], 0)

    async def test_poll_notion_review_pending_unmatched_reviewer_skipped(self):
        self.mock_notion.query_assets_database.return_value = [
            {
                "notion_page_id": "page_unmatched",
                "page_title": "Clip B",
                "asset_url": "https://vimeo.com/9992",
                "page_url": "https://notion.so/page_2",
                "status": "Review Pending",
                "reviewers": ["UnknownReviewer"],
                "editors": [],
            }
        ]
        await self.scheduler.poll_notion_once()
        self.mock_bot.send_review_alert.assert_not_called()
        self.assertIsNone(self.db.get_notion_item("page_unmatched:REVIEW"))

    async def test_poll_notion_edit_pending(self):
        self.mock_notion.query_assets_database.return_value = [
            {
                "notion_page_id": "page_edit_1",
                "page_title": "Video Edit A",
                "asset_url": "https://vimeo.com/edit1",
                "page_url": "https://notion.so/edit_1",
                "status": "Edit Pending",
                "reviewers": [],
                "editors": ["Roger", "Kai"],
            }
        ]
        mock_msg = MagicMock()
        mock_msg.id = 666001
        self.mock_bot.send_edit_alert.return_value = mock_msg

        await self.scheduler.poll_notion_once()

        self.mock_bot.send_edit_alert.assert_called_once_with(
            channel_id=987654,
            page_title="Video Edit A",
            asset_url="https://vimeo.com/edit1",
            page_url="https://notion.so/edit_1",
            editors=["Roger", "Kai"],
        )

        item = self.db.get_notion_item("page_edit_1:EDIT")
        self.assertIsNotNone(item)
        self.assertEqual(item["category"], "EDIT")
        self.assertEqual(item["status"], "PENDING")

    async def test_poll_notion_edit_pending_empty_editor_skipped(self):
        self.mock_notion.query_assets_database.return_value = [
            {
                "notion_page_id": "page_no_editor",
                "page_title": "Video Edit No Editor",
                "asset_url": "https://vimeo.com/edit2",
                "page_url": "https://notion.so/edit_2",
                "status": "Edit Pending",
                "reviewers": [],
                "editors": [],
            }
        ]
        await self.scheduler.poll_notion_once()
        self.mock_bot.send_edit_alert.assert_not_called()
        self.assertIsNone(self.db.get_notion_item("page_no_editor:EDIT"))

    async def test_poll_notion_resolves_when_status_changes(self):
        self.db.record_notion_item(
            notion_page_id="page_done",
            category="REVIEW",
            page_title="Finished Clip",
            asset_url="https://vimeo.com/done",
            notion_status="Review Pending",
            assigned_users="Roger",
            discord_channel_id=987654,
            discord_message_id=888001,
        )

        self.mock_notion.query_assets_database.return_value = [
            {
                "notion_page_id": "page_done",
                "page_title": "Finished Clip",
                "asset_url": "https://vimeo.com/done",
                "page_url": "https://notion.so/done",
                "status": "Ready to post",
                "reviewers": ["Roger"],
                "editors": [],
            }
        ]

        await self.scheduler.poll_notion_once()

        item = self.db.get_notion_item("page_done:REVIEW")
        self.assertEqual(item["status"], "RESOLVED")
        self.mock_bot.update_resolved_message.assert_called_once_with(
            channel_id=987654,
            message_id=888001,
            page_title="Finished Clip",
            category="REVIEW",
            current_status="Ready to post",
        )

    async def test_poll_notion_ready_to_post(self):
        # test ready to post alert
        self.mock_notion.query_assets_database.return_value = [
            {
                "notion_page_id": "page_post_1",
                "page_title": "Final Clip Ready",
                "asset_url": "https://vimeo.com/post1",
                "page_url": "https://notion.so/post_1",
                "status": "Ready to Post",
                "reviewers": [],
                "editors": [],
                "uploaders": ["Elvis"],
            }
        ]
        mock_msg = MagicMock()
        mock_msg.id = 777099
        self.mock_bot.send_ready_to_post_alert.return_value = mock_msg

        await self.scheduler.poll_notion_once()

        self.mock_bot.send_ready_to_post_alert.assert_called_once_with(
            channel_id=987654,
            page_title="Final Clip Ready",
            asset_url="https://vimeo.com/post1",
            page_url="https://notion.so/post_1",
            uploaders=["Elvis"],
        )

        item = self.db.get_notion_item("page_post_1:POST")
        self.assertIsNotNone(item)
        self.assertEqual(item["category"], "POST")
        self.assertEqual(item["status"], "PENDING")

    async def test_poll_notion_ready_to_post_empty_uploader_skipped(self):
        # test skip empty uploader
        self.mock_notion.query_assets_database.return_value = [
            {
                "notion_page_id": "page_post_no_uploader",
                "page_title": "Clip Without Uploader",
                "asset_url": "https://vimeo.com/post2",
                "page_url": "https://notion.so/post_2",
                "status": "Ready to Post",
                "reviewers": [],
                "editors": [],
                "uploaders": [],
            }
        ]
        await self.scheduler.poll_notion_once()
        self.mock_bot.send_ready_to_post_alert.assert_not_called()
        self.assertIsNone(self.db.get_notion_item("page_post_no_uploader:POST"))

    async def test_poll_notion_ready_to_post_resolves_when_scheduled(self):
        # test resolves on scheduled
        self.db.record_notion_item(
            notion_page_id="page_post_sched",
            category="POST",
            page_title="Clip Being Scheduled",
            asset_url="https://vimeo.com/post3",
            notion_status="Ready to Post",
            assigned_users="Elvis",
            discord_channel_id=987654,
            discord_message_id=777100,
        )

        self.mock_notion.query_assets_database.return_value = [
            {
                "notion_page_id": "page_post_sched",
                "page_title": "Clip Being Scheduled",
                "asset_url": "https://vimeo.com/post3",
                "page_url": "https://notion.so/post_3",
                "status": "Scheduled",
                "reviewers": [],
                "editors": [],
                "uploaders": ["Elvis"],
            }
        ]

        await self.scheduler.poll_notion_once()

        item = self.db.get_notion_item("page_post_sched:POST")
        self.assertEqual(item["status"], "RESOLVED")
        self.mock_bot.update_resolved_message.assert_called_once_with(
            channel_id=987654,
            message_id=777100,
            page_title="Clip Being Scheduled",
            category="POST",
            current_status="Scheduled",
        )

    async def test_check_escalations_due_review(self):
        t_past = datetime.now(timezone.utc) - timedelta(hours=13)
        self.db.record_notion_item(
            notion_page_id="page_overdue_rev",
            category="REVIEW",
            page_title="Overdue Review Clip",
            asset_url="https://vimeo.com/due_rev",
            notion_status="Review Pending",
            assigned_users="Roger,Kai",
            discord_channel_id=987654,
            discord_message_id=777001,
            page_url="https://notion.so/due_rev",
            initial_time=t_past,
        )

        self.mock_bot.send_notion_escalation.return_value = True

        await self.scheduler.check_escalations_once()

        self.mock_bot.send_notion_escalation.assert_called_once_with(
            channel_id=987654,
            message_id=777001,
            stage=0,
            category="REVIEW",
            page_title="Overdue Review Clip",
            asset_url="https://vimeo.com/due_rev",
            page_url="https://notion.so/due_rev",
            assigned_users=["Roger", "Kai"],
        )

        updated = self.db.get_notion_item("page_overdue_rev:REVIEW")
        self.assertEqual(updated["stage"], 1)

    async def test_check_escalations_skips_edit_items(self):
        # editors are never escalated
        t_past = datetime.now(timezone.utc) - timedelta(hours=25)
        self.db.record_notion_item(
            notion_page_id="page_overdue_edit",
            category="EDIT",
            page_title="Overdue Edit Clip",
            asset_url="https://vimeo.com/due_edit",
            notion_status="Edit Pending",
            assigned_users="Elvis",
            discord_channel_id=987654,
            discord_message_id=777002,
            page_url="https://notion.so/due_edit",
            initial_time=t_past,
        )

        self.mock_bot.send_notion_escalation.return_value = True

        await self.scheduler.check_escalations_once()

        self.mock_bot.send_notion_escalation.assert_not_called()
        updated = self.db.get_notion_item("page_overdue_edit:EDIT")
        self.assertEqual(updated["stage"], 0)

    async def test_check_escalations_due_post_24h(self):
        # test post 24h escalation
        t_past = datetime.now(timezone.utc) - timedelta(hours=25)
        self.db.record_notion_item(
            notion_page_id="page_overdue_post",
            category="POST",
            page_title="Overdue Post Clip",
            asset_url="https://vimeo.com/due_post",
            notion_status="Ready to Post",
            assigned_users="Elvis",
            discord_channel_id=987654,
            discord_message_id=777009,
            page_url="https://notion.so/due_post",
            initial_time=t_past,
        )

        self.mock_bot.send_notion_escalation.return_value = True

        await self.scheduler.check_escalations_once()

        self.mock_bot.send_notion_escalation.assert_called_once_with(
            channel_id=987654,
            message_id=777009,
            stage=0,
            category="POST",
            page_title="Overdue Post Clip",
            asset_url="https://vimeo.com/due_post",
            page_url="https://notion.so/due_post",
            assigned_users=["Elvis"],
        )

        updated = self.db.get_notion_item("page_overdue_post:POST")
        self.assertEqual(updated["stage"], 1)

    async def test_snooze_skips_escalations(self):
        t_past = datetime.now(timezone.utc) - timedelta(hours=13)
        self.db.record_notion_item(
            notion_page_id="page_snoozed",
            category="REVIEW",
            page_title="Snoozed Clip",
            asset_url="https://vimeo.com/snoozed",
            notion_status="Review Pending",
            assigned_users="Roger",
            discord_channel_id=987654,
            discord_message_id=777003,
            initial_time=t_past,
        )

        # activate snooze
        self.db.set_snooze(datetime.now(timezone.utc) + timedelta(hours=12))

        await self.scheduler.check_escalations_once()
        self.mock_bot.send_notion_escalation.assert_not_called()

    async def test_check_escalations_per_user_snooze_partial(self):
        # partial snooze keeps active
        t_past = datetime.now(timezone.utc) - timedelta(hours=13)
        self.db.record_notion_item(
            notion_page_id="page_partial_snooze",
            category="REVIEW",
            page_title="Partial Snooze Clip",
            asset_url="https://vimeo.com/partial",
            notion_status="Review Pending",
            assigned_users="Roger,Kai",
            discord_channel_id=987654,
            discord_message_id=777004,
            initial_time=t_past,
        )

        self.mock_bot.is_target_snoozed.side_effect = lambda u, guild: u == "Roger"
        self.mock_bot.send_notion_escalation.return_value = True

        await self.scheduler.check_escalations_once()

        self.mock_bot.send_notion_escalation.assert_called_once_with(
            channel_id=987654,
            message_id=777004,
            stage=0,
            category="REVIEW",
            page_title="Partial Snooze Clip",
            asset_url="https://vimeo.com/partial",
            page_url="",
            assigned_users=["Kai"],
        )

    async def test_check_escalations_per_user_snooze_all(self):
        # all snoozed skips ping
        t_past = datetime.now(timezone.utc) - timedelta(hours=13)
        self.db.record_notion_item(
            notion_page_id="page_all_snooze",
            category="REVIEW",
            page_title="All Snoozed Clip",
            asset_url="https://vimeo.com/all_snoozed",
            notion_status="Review Pending",
            assigned_users="Roger,Kai",
            discord_channel_id=987654,
            discord_message_id=777005,
            initial_time=t_past,
        )

        self.mock_bot.is_target_snoozed.return_value = True

        await self.scheduler.check_escalations_once()
        self.mock_bot.send_notion_escalation.assert_not_called()

    async def test_check_custom_reminders_due(self):
        t_past = datetime.now(timezone.utc) - timedelta(hours=13)
        self.db.record_custom_reminder(
            reminder_id="custom_overdue",
            message_text="Deliver thumbnail concepts",
            discord_guild_id=1504290249717710919,
            discord_channel_id=987654,
            discord_message_id=999001,
            target_user_id="264314531818242049",
            created_by_user_id="259869751130587137",
            initial_time=t_past,
        )

        self.mock_bot.send_custom_escalation_ping.return_value = True

        await self.scheduler.check_custom_reminders_once()

        self.mock_bot.send_custom_escalation_ping.assert_called_once_with(
            channel_id=987654,
            message_id=999001,
            stage=0,
            target_user_id="264314531818242049",
            message_text="Deliver thumbnail concepts",
        )

        updated = self.db.get_custom_reminder("custom_overdue")
        self.assertEqual(updated["stage"], 1)

    async def test_reconcile_custom_reminders_offline_heart(self):
        # test offline heart reconciliation
        self.db.record_custom_reminder(
            reminder_id="rem_offline",
            message_text="go to sleep",
            discord_guild_id=1504290249717710919,
            discord_channel_id=987654,
            discord_message_id=999888,
            target_user_id="259869751130587137",
            created_by_user_id="264314531818242049",
        )

        mock_user = MagicMock()
        mock_user.id = 259869751130587137
        self.mock_bot.user = MagicMock()
        self.mock_bot.user.id = 999999999

        mock_reaction = MagicMock()
        mock_reaction.emoji = MagicMock()
        mock_reaction.emoji.name = "❤️"

        async def user_generator():
            yield mock_user

        mock_reaction.users = MagicMock(return_value=user_generator())

        mock_msg = MagicMock()
        mock_msg.reactions = [mock_reaction]

        mock_channel = MagicMock()
        mock_channel.fetch_message = AsyncMock(return_value=mock_msg)
        self.mock_bot.get_channel = MagicMock(return_value=mock_channel)
        self.mock_bot.update_acknowledged_reminder_message = AsyncMock()

        await self.scheduler.reconcile_custom_reminders_once()

        rem = self.db.get_custom_reminder("rem_offline")
        self.assertEqual(rem["status"], "REVIEWED")
        self.mock_bot.update_acknowledged_reminder_message.assert_called_once()


if __name__ == "__main__":
    unittest.main()
