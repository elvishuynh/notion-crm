import unittest
from unittest.mock import MagicMock
import tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta

from db import ReviewDatabase
from bot import VertigohVisionBot, TEAM_ROLE_IDS


class TestVertigohVisionBot(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test_bot.db")
        self.db = ReviewDatabase(self.db_path)

        self.bot = VertigohVisionBot(
            db=self.db,
            reviewer_id="reviewer_123",
            guild_id=1504290249717710919,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_resolve_role_mentions_known_team(self):
        # test known team role mentions
        mentions = self.bot.resolve_role_mentions(["Roger", "Kai", "Elvis"])
        self.assertIn(f"<@&{TEAM_ROLE_IDS['roger']}>", mentions)
        self.assertIn(f"<@&{TEAM_ROLE_IDS['kai']}>", mentions)
        self.assertIn(f"<@&{TEAM_ROLE_IDS['elvis']}>", mentions)

    def test_resolve_role_mentions_case_insensitivity(self):
        # test case insensitive role lookup
        mentions = self.bot.resolve_role_mentions(["kAi", "ROGER"])
        self.assertEqual(
            mentions,
            f"<@&{TEAM_ROLE_IDS['kai']}> <@&{TEAM_ROLE_IDS['roger']}>",
        )

    def test_resolve_role_mentions_fallback(self):
        # test fallback for unknown names
        mentions = self.bot.resolve_role_mentions(["RandomEditor"])
        self.assertEqual(mentions, "@RandomEditor")

    def test_resolve_role_mentions_empty(self):
        # test fallback on empty list
        mentions = self.bot.resolve_role_mentions([])
        self.assertEqual(mentions, "@everyone")

    def test_resolve_role_mentions_with_guild_role(self):
        # test dynamic guild role lookup
        mock_guild = MagicMock()
        mock_role = MagicMock()
        mock_role.name = "Animator"
        mock_role.id = 998877
        mock_guild.roles = [mock_role]

        mentions = self.bot.resolve_role_mentions(["animator"], guild=mock_guild)
        self.assertEqual(mentions, "<@&998877>")

    def test_is_target_snoozed_team_role(self):
        # test role mapping to user snooze
        self.assertFalse(self.bot.is_target_snoozed("Roger"))

        # snooze roger user id
        self.db.set_user_snooze("221159271738376192", datetime.now(timezone.utc) + timedelta(hours=12))
        self.assertTrue(self.bot.is_target_snoozed("Roger"))
        self.assertTrue(self.bot.is_target_snoozed("roger"))

        # kai should not be affected
        self.assertFalse(self.bot.is_target_snoozed("Kai"))

    async def test_send_ready_to_post_alert(self):
        # test ready to post alert
        mock_channel = MagicMock()
        mock_channel.send = MagicMock()
        from unittest.mock import AsyncMock
        mock_channel.send = AsyncMock()
        self.bot.get_channel = MagicMock(return_value=mock_channel)

        await self.bot.send_ready_to_post_alert(
            channel_id=12345,
            page_title="Teaser Ep 1",
            asset_url="https://vimeo.com/teaser1",
            page_url="https://notion.so/teaser1",
            uploaders=["Elvis"],
        )

        mock_channel.send.assert_called_once()
        call_kwargs = mock_channel.send.call_args.kwargs
        self.assertIn("Teaser Ep 1", call_kwargs["content"])
        self.assertIn(f"<@&{TEAM_ROLE_IDS['elvis']}>", call_kwargs["content"])
        embed = call_kwargs["embed"]
        self.assertEqual(embed.title, "“Teaser Ep 1”")
        self.assertEqual(embed.fields[0].name, "Poster")
        self.assertEqual(embed.fields[1].name, "Status")
        self.assertEqual(embed.footer.text, "Next remind in 24h")

    async def test_send_notion_escalation_post(self):
        # test post escalation content
        mock_channel = MagicMock()
        mock_msg = MagicMock()
        from unittest.mock import AsyncMock
        mock_msg.reply = AsyncMock()
        mock_msg.edit = AsyncMock()
        mock_msg.embeds = [MagicMock()]
        mock_channel.fetch_message = AsyncMock(return_value=mock_msg)
        self.bot.get_channel = MagicMock(return_value=mock_channel)

        success = await self.bot.send_notion_escalation(
            channel_id=12345,
            message_id=999,
            stage=0,
            category="POST",
            page_title="Teaser Ep 1",
            asset_url="https://vimeo.com/teaser1",
            page_url="https://notion.so/teaser1",
            assigned_users=["Elvis"],
        )

        self.assertTrue(success)
        mock_msg.reply.assert_called_once()
        reply_content = mock_msg.reply.call_args.kwargs["content"]
        self.assertIn("[24h Ready to Post Reminder #1]", reply_content)
        self.assertIn(f"<@&{TEAM_ROLE_IDS['elvis']}>", reply_content)

    async def test_update_resolved_message_no_reply(self):
        # test resolved edits without reply
        mock_channel = MagicMock()
        mock_msg = MagicMock()
        from unittest.mock import AsyncMock
        mock_msg.edit = AsyncMock()
        mock_msg.reply = AsyncMock()

        old_embed = MagicMock()
        old_embed.description = "Asset ready for review on Notion schedule board."
        field_role = MagicMock()
        field_role.name = "Reviewer"
        field_role.value = "<@&1559491606653173760>"
        field_role.inline = True
        field_status = MagicMock()
        field_status.name = "Status"
        field_status.value = "Review Pending"
        field_status.inline = True
        old_embed.fields = [field_role, field_status]
        mock_msg.embeds = [old_embed]

        mock_channel.fetch_message = AsyncMock(return_value=mock_msg)
        self.bot.get_channel = MagicMock(return_value=mock_channel)

        await self.bot.update_resolved_message(
            channel_id=12345,
            message_id=999,
            page_title="Teaser Ep 1",
            category="REVIEW",
            current_status="Edit Pending",
        )

        # verify edited and no reply sent
        mock_msg.edit.assert_called_once()
        mock_msg.reply.assert_not_called()
        new_embed = mock_msg.edit.call_args.kwargs["embed"]
        self.assertEqual(new_embed.title, "“Teaser Ep 1”")
        self.assertEqual(new_embed.fields[1].value, "✅ Resolved (Edit Pending)")
        self.assertEqual(new_embed.footer.text, "Resolved via Notion update • Reminders stopped")

    def test_resolve_status_from_emoji(self):
        # test emoji mapping
        self.assertEqual(self.bot.resolve_status_from_emoji("👀"), "Review Pending")
        self.assertEqual(self.bot.resolve_status_from_emoji("🎬"), "Edit Pending")
        self.assertEqual(self.bot.resolve_status_from_emoji("↗️"), "Ready to Post")
        self.assertEqual(self.bot.resolve_status_from_emoji("↗"), "Ready to Post")
        self.assertEqual(self.bot.resolve_status_from_emoji("✅"), "Posted")
        self.assertIsNone(self.bot.resolve_status_from_emoji("👌"))
        self.assertIsNone(self.bot.resolve_status_from_emoji("✏️"))
        self.assertIsNone(self.bot.resolve_status_from_emoji("📅"))

    async def test_on_raw_reaction_add_updates_notion(self):
        # test reaction updates notion
        from unittest.mock import AsyncMock
        mock_notion = MagicMock()
        mock_notion.update_page_status = MagicMock(return_value=True)
        self.bot.notion = mock_notion
        mock_scheduler = MagicMock()
        mock_scheduler.poll_notion_once = AsyncMock()
        self.bot.scheduler = mock_scheduler

        self.db.record_notion_item(
            notion_page_id="page_react_1",
            category="REVIEW",
            page_title="Reel To Post",
            asset_url="https://vimeo.com/react",
            notion_status="Review Pending",
            assigned_users="Elvis",
            discord_channel_id=123,
            discord_message_id=999888,
        )

        mock_payload = MagicMock()
        mock_payload.user_id = 111222
        mock_payload.message_id = 999888
        mock_payload.channel_id = 123
        mock_payload.emoji = MagicMock()
        mock_payload.emoji.name = "↗️"

        await self.bot.on_raw_reaction_add(mock_payload)

        mock_notion.update_page_status.assert_called_once_with("page_react_1", "Ready to Post")
        mock_scheduler.poll_notion_once.assert_called_once()

    async def test_send_review_alert_format(self):
        # test alert format details
        from unittest.mock import AsyncMock, call
        mock_msg = MagicMock()
        mock_msg.add_reaction = AsyncMock()
        mock_channel = MagicMock()
        mock_channel.send = AsyncMock(return_value=mock_msg)
        self.bot.get_channel = MagicMock(return_value=mock_channel)

        await self.bot.send_review_alert(
            channel_id=123,
            page_title="Summer Campaign",
            asset_url="https://vimeo.com/summer",
            page_url="https://notion.so/summer",
            reviewers=["Roger"],
        )

        call_args = mock_channel.send.call_args
        content = call_args.kwargs["content"]
        embed = call_args.kwargs["embed"]

        # no exclamation mark outside embed
        self.assertFalse(content.endswith("!"))
        self.assertIn("is ready for review", content)

        # title links to asset
        self.assertEqual(embed.title, "“Summer Campaign”")
        self.assertEqual(embed.url, "https://vimeo.com/summer")

        # description has required action and content schedule
        self.assertIn("Required Action: REVIEW", embed.description)
        self.assertIn("[Content Schedule](https://notion.so/summer)", embed.description)

        # status field has hourglass
        self.assertEqual(embed.fields[1].name, "Status")
        self.assertEqual(embed.fields[1].value, "⏳ Review Pending")

        # pre-added reactions
        mock_msg.add_reaction.assert_has_calls([call("👀"), call("🎬"), call("↗️"), call("✅")])




if __name__ == "__main__":
    unittest.main()
