import unittest
from unittest.mock import AsyncMock, MagicMock
import tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta

from db import ReviewDatabase, calculate_next_ping, from_iso
from scheduler import ReviewScheduler


class TestReviewScheduler(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test_scheduler.db")
        self.db = ReviewDatabase(self.db_path)

        self.mock_bot = MagicMock()
        self.mock_bot.send_asset_alert = AsyncMock()
        self.mock_bot.send_escalation_ping = AsyncMock()
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

    async def test_poll_notion_new_asset(self):
        # seed baseline item
        self.db.seed_baseline_asset("base_page", "Baseline", "https://base.com")

        self.mock_notion.query_assets_database.return_value = [
            {
                "notion_page_id": "page_1",
                "page_title": "Promo Clip A",
                "asset_url": "https://vimeo.com/9991",
                "page_url": "https://notion.so/page_1",
            }
        ]
        mock_msg = MagicMock()
        mock_msg.id = 555001
        self.mock_bot.send_asset_alert.return_value = mock_msg

        await self.scheduler.poll_notion_once()

        # verify alert dispatched
        self.mock_bot.send_asset_alert.assert_called_once_with(
            channel_id=987654,
            page_title="Promo Clip A",
            asset_url="https://vimeo.com/9991",
            page_url="https://notion.so/page_1",
            reviewer_id="reviewer_123",
        )

        # verify record saved
        asset = self.db.get_asset("page_1")
        self.assertIsNotNone(asset)
        self.assertEqual(asset["discord_message_id"], 555001)
        self.assertEqual(asset["status"], "PENDING")
        self.assertEqual(asset["stage"], 0)

    async def test_poll_notion_empty_db_seeds_baseline(self):
        # empty db baseline seed
        self.mock_notion.query_assets_database.return_value = [
            {
                "notion_page_id": "historical_1",
                "page_title": "Old Clip",
                "asset_url": "https://vimeo.com/old",
                "page_url": "https://notion.so/old",
            }
        ]
        await self.scheduler.poll_notion_once()

        # verify alert skipped
        self.mock_bot.send_asset_alert.assert_not_called()

        # verify baseline marked
        asset = self.db.get_asset("historical_1")
        self.assertIsNotNone(asset)
        self.assertEqual(asset["status"], "REVIEWED")
        self.assertEqual(asset["reviewed_by"], "BASELINE_SEED")

    async def test_poll_notion_already_tracked_skipped(self):
        # existing asset
        self.db.record_new_asset(
            notion_page_id="page_1",
            page_title="Promo Clip A",
            asset_url="https://vimeo.com/9991",
            discord_channel_id=987654,
            discord_message_id=555001,
        )
        self.mock_notion.query_assets_database.return_value = [
            {
                "notion_page_id": "page_1",
                "page_title": "Promo Clip A",
                "asset_url": "https://vimeo.com/9991",
                "page_url": "https://notion.so/page_1",
            }
        ]

        await self.scheduler.poll_notion_once()
        self.mock_bot.send_asset_alert.assert_not_called()

    async def test_check_escalations_due(self):
        # overdue asset
        t_past = datetime.now(timezone.utc) - timedelta(hours=13)
        self.db.record_new_asset(
            notion_page_id="page_overdue",
            page_title="Overdue Clip",
            asset_url="https://vimeo.com/due",
            discord_channel_id=987654,
            discord_message_id=777001,
            initial_time=t_past,
        )

        self.mock_bot.send_escalation_ping.return_value = True

        await self.scheduler.check_escalations_once()

        self.mock_bot.send_escalation_ping.assert_called_once_with(
            channel_id=987654,
            message_id=777001,
            stage=0,
            reviewer_id="reviewer_123",
            page_title="Overdue Clip",
            asset_url="https://vimeo.com/due",
        )

        # verify stage updated
        updated = self.db.get_asset("page_overdue")
        self.assertEqual(updated["stage"], 1)

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

        # verify reminder stage updated
        updated = self.db.get_custom_reminder("custom_overdue")
        self.assertEqual(updated["stage"], 1)


if __name__ == "__main__":
    unittest.main()
