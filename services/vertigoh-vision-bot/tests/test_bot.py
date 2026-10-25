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
        self.assertEqual(embed.footer.text, "Next remind in 12h")

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
        self.assertIn("Reminder #1 — Ready to post", reply_content)
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

    def test_is_heart_emoji(self):
        # test heart emoji recognition
        from bot import is_heart_emoji
        self.assertTrue(is_heart_emoji("❤️"))
        self.assertTrue(is_heart_emoji("♥️"))
        self.assertTrue(is_heart_emoji("💖"))
        self.assertTrue(is_heart_emoji("💗"))
        self.assertTrue(is_heart_emoji("💓"))
        self.assertTrue(is_heart_emoji("heart"))
        self.assertTrue(is_heart_emoji(":heart:"))
        self.assertTrue(is_heart_emoji("sparkling_heart"))
        self.assertFalse(is_heart_emoji("👀"))
        self.assertFalse(is_heart_emoji("✅"))
        self.assertFalse(is_heart_emoji("random_emoji"))

    async def test_on_raw_reaction_add_heart_stops_custom_reminder(self):
        # test heart stops reminder
        from unittest.mock import AsyncMock
        self.db.record_custom_reminder(
            reminder_id="rem_heart_test",
            message_text="go to sleep",
            discord_guild_id=123,
            discord_channel_id=456,
            discord_message_id=888999,
            target_user_id="259869751130587137",
            created_by_user_id="264314531818242049",
        )

        mock_msg = MagicMock()
        mock_msg.embeds = [MagicMock()]
        mock_msg.edit = AsyncMock()
        mock_msg.reply = AsyncMock()
        mock_channel = MagicMock()
        mock_channel.fetch_message = AsyncMock(return_value=mock_msg)
        self.bot.get_channel = MagicMock(return_value=mock_channel)

        mock_user = MagicMock()
        mock_user.id = 259869751130587137
        mock_user.mention = "<@259869751130587137>"
        self.bot.get_user = MagicMock(return_value=mock_user)

        mock_payload = MagicMock()
        mock_payload.user_id = 259869751130587137
        mock_payload.message_id = 888999
        mock_payload.channel_id = 456
        mock_payload.emoji = MagicMock()
        mock_payload.emoji.name = "❤️"

        await self.bot.on_raw_reaction_add(mock_payload)

        # verify status updated in db
        rem = self.db.get_custom_reminder("rem_heart_test")
        self.assertEqual(rem["status"], "REVIEWED")
        self.assertEqual(rem["acknowledged_by"], "259869751130587137")
        mock_msg.edit.assert_called_once()
        mock_msg.reply.assert_called_once()

    async def test_on_raw_reaction_add_heart_on_ping_stops_custom_reminder(self):
        # test heart on ping message
        from unittest.mock import AsyncMock
        self.db.record_custom_reminder(
            reminder_id="rem_ping_heart",
            message_text="go to sleep",
            discord_guild_id=123,
            discord_channel_id=456,
            discord_message_id=888001,
            target_user_id="259869751130587137",
            created_by_user_id="264314531818242049",
        )
        self.db.record_custom_reminder_ping(
            ping_message_id=999002,
            reminder_id="rem_ping_heart",
            discord_message_id=888001,
        )

        orig_msg = MagicMock()
        orig_msg.embeds = [MagicMock()]
        orig_msg.edit = AsyncMock()

        ping_msg = MagicMock()
        ping_msg.reply = AsyncMock()

        async def fetch_side_effect(mid):
            if mid == 888001:
                return orig_msg
            return ping_msg

        mock_channel = MagicMock()
        mock_channel.fetch_message = AsyncMock(side_effect=fetch_side_effect)
        self.bot.get_channel = MagicMock(return_value=mock_channel)

        mock_user = MagicMock()
        mock_user.id = 259869751130587137
        mock_user.mention = "<@259869751130587137>"
        self.bot.get_user = MagicMock(return_value=mock_user)

        mock_payload = MagicMock()
        mock_payload.user_id = 259869751130587137
        mock_payload.message_id = 999002
        mock_payload.channel_id = 456
        mock_payload.emoji = MagicMock()
        mock_payload.emoji.name = "❤️"

        await self.bot.on_raw_reaction_add(mock_payload)

        # verify reminder stopped
        rem = self.db.get_custom_reminder("rem_ping_heart")
        self.assertEqual(rem["status"], "REVIEWED")
        orig_msg.edit.assert_called_once()
        ping_msg.reply.assert_called_once()
        self.assertIn("Task stopped", ping_msg.reply.call_args[0][0])

    async def test_on_message_reply_with_heart_stops_reminder(self):
        # test reply with heart
        from unittest.mock import AsyncMock
        self.db.record_custom_reminder(
            reminder_id="rem_reply_test",
            message_text="go to sleep",
            discord_guild_id=123,
            discord_channel_id=456,
            discord_message_id=888333,
            target_user_id="259869751130587137",
            created_by_user_id="264314531818242049",
        )

        mock_msg = MagicMock()
        mock_msg.embeds = [MagicMock()]
        mock_msg.edit = AsyncMock()
        mock_msg.reply = AsyncMock()

        mock_channel = MagicMock()
        mock_channel.id = 456
        mock_channel.fetch_message = AsyncMock(return_value=mock_msg)
        self.bot.get_channel = MagicMock(return_value=mock_channel)

        user_msg = MagicMock()
        user_msg.author = MagicMock()
        user_msg.author.id = 259869751130587137
        user_msg.author.mention = "<@259869751130587137>"
        user_msg.content = "❤️"
        user_msg.channel = mock_channel
        user_msg.reference = MagicMock()
        user_msg.reference.message_id = 888333

        await self.bot.on_message(user_msg)

        rem = self.db.get_custom_reminder("rem_reply_test")
        self.assertEqual(rem["status"], "REVIEWED")
        mock_msg.edit.assert_called_once()

    async def test_create_custom_reminder_adds_heart_reaction(self):
        # test reminder pre-adds heart
        from unittest.mock import AsyncMock
        mock_msg = MagicMock()
        mock_msg.id = 777111
        mock_msg.add_reaction = AsyncMock()
        mock_channel = MagicMock()
        mock_channel.id = 123
        mock_channel.guild = MagicMock()
        mock_channel.guild.id = 456
        mock_channel.send = AsyncMock(return_value=mock_msg)

        target = MagicMock()
        target.id = 111
        target.display_name = "Elvis"
        target.mention = "<@111>"

        author = MagicMock()
        author.id = 222
        author.mention = "<@222>"

        sent = await self.bot.create_custom_reminder(
            channel=mock_channel,
            target_user=target,
            message_text="go to sleep",
            author_user=author,
        )

        self.assertIsNotNone(sent)
        mock_msg.add_reaction.assert_called_once_with("❤️")
        embed = mock_channel.send.call_args.kwargs["embed"]
        self.assertEqual(embed.footer.text, "React with ❤️ to stop reminder pings")

    async def test_send_custom_escalation_ping_adds_heart_and_records_ping(self):
        # test ping pre-adds heart
        from unittest.mock import AsyncMock
        self.db.record_custom_reminder(
            reminder_id="remind_555",
            message_text="go to sleep",
            discord_guild_id=123,
            discord_channel_id=456,
            discord_message_id=555,
            target_user_id="111",
            created_by_user_id="222",
        )

        orig_msg = MagicMock()
        ping_msg = MagicMock()
        ping_msg.id = 999555
        ping_msg.add_reaction = AsyncMock()
        orig_msg.reply = AsyncMock(return_value=ping_msg)

        mock_channel = MagicMock()
        mock_channel.fetch_message = AsyncMock(return_value=orig_msg)
        self.bot.get_channel = MagicMock(return_value=mock_channel)

        success = await self.bot.send_custom_escalation_ping(
            channel_id=456,
            message_id=555,
            stage=0,
            target_user_id="111",
            message_text="go to sleep",
        )

        self.assertTrue(success)
        ping_msg.add_reaction.assert_called_once_with("❤️")
        pings = self.db.get_custom_reminder_pings("remind_555")
        self.assertEqual(len(pings), 1)
        self.assertEqual(pings[0]["ping_message_id"], 999555)

    async def test_send_notion_escalation_adds_reactions_and_records_ping(self):
        # test notion escalation pre-adds reactions
        from unittest.mock import AsyncMock
        self.db.record_notion_item(
            notion_page_id="page_post_alert",
            category="POST",
            page_title="Gr818ers x WSS Event FPV final1",
            asset_url="https://vimeo.com/gr818",
            notion_status="Ready to Post",
            assigned_users="Elvis",
            discord_channel_id=456,
            discord_message_id=555,
        )

        orig_msg = MagicMock()
        orig_msg.embeds = [MagicMock()]
        orig_msg.edit = AsyncMock()
        ping_msg = MagicMock()
        ping_msg.id = 999666
        ping_msg.add_reaction = AsyncMock()
        orig_msg.reply = AsyncMock(return_value=ping_msg)

        mock_channel = MagicMock()
        mock_channel.fetch_message = AsyncMock(return_value=orig_msg)
        self.bot.get_channel = MagicMock(return_value=mock_channel)

        success = await self.bot.send_notion_escalation(
            channel_id=456,
            message_id=555,
            stage=0,
            category="POST",
            page_title="Gr818ers x WSS Event FPV final1",
            asset_url="https://vimeo.com/gr818",
            page_url="https://app.notion.com/p/gr818",
            assigned_users=["Elvis"],
        )

        self.assertTrue(success)
        # verify reactions added
        ping_msg.add_reaction.assert_any_call("✅")
        ping_msg.add_reaction.assert_any_call("❤️")
        # verify ping recorded
        pings = self.db.get_notion_item_pings("page_post_alert:POST")
        self.assertEqual(len(pings), 1)
        self.assertEqual(pings[0]["ping_message_id"], 999666)

    async def test_send_notion_escalation_rolling_deletes_previous_ping(self):
        # test rolling single alert
        from unittest.mock import AsyncMock
        self.db.record_notion_item(
            notion_page_id="page_rolling_test",
            category="REVIEW",
            page_title="Interview Ep 2",
            asset_url="https://vimeo.com/ep2",
            notion_status="Review Pending",
            assigned_users="Roger",
            discord_channel_id=456,
            discord_message_id=555,
        )

        orig_msg = MagicMock()
        orig_msg.embeds = [MagicMock()]
        orig_msg.edit = AsyncMock()

        ping_msg1 = MagicMock()
        ping_msg1.id = 1001
        ping_msg1.delete = AsyncMock()
        ping_msg1.add_reaction = AsyncMock()

        ping_msg2 = MagicMock()
        ping_msg2.id = 1002
        ping_msg2.delete = AsyncMock()
        ping_msg2.add_reaction = AsyncMock()

        orig_msg.reply = AsyncMock(side_effect=[ping_msg1, ping_msg2])

        mock_channel = MagicMock()
        async def fetch_msg(mid):
            if mid == 555:
                return orig_msg
            if mid == 1001:
                return ping_msg1
            if mid == 1002:
                return ping_msg2
            return None
        mock_channel.fetch_message = AsyncMock(side_effect=fetch_msg)
        self.bot.get_channel = MagicMock(return_value=mock_channel)

        await self.bot.send_notion_escalation(
            channel_id=456,
            message_id=555,
            stage=0,
            category="REVIEW",
            page_title="Interview Ep 2",
            asset_url="https://vimeo.com/ep2",
            page_url="https://notion.so/ep2",
            assigned_users=["Roger"],
        )
        pings = self.db.get_notion_item_pings("page_rolling_test:REVIEW")
        self.assertEqual(len(pings), 1)
        self.assertEqual(pings[0]["ping_message_id"], 1001)

        await self.bot.send_notion_escalation(
            channel_id=456,
            message_id=555,
            stage=1,
            category="REVIEW",
            page_title="Interview Ep 2",
            asset_url="https://vimeo.com/ep2",
            page_url="https://notion.so/ep2",
            assigned_users=["Roger"],
        )
        ping_msg1.delete.assert_called_once()
        pings_after = self.db.get_notion_item_pings("page_rolling_test:REVIEW")
        self.assertEqual(len(pings_after), 1)
        self.assertEqual(pings_after[0]["ping_message_id"], 1002)

    async def test_send_custom_escalation_rolling_deletes_previous_ping(self):
        # test rolling custom ping
        from unittest.mock import AsyncMock
        self.db.record_custom_reminder(
            reminder_id="remind_rolling",
            message_text="Deliver export",
            discord_guild_id=123,
            discord_channel_id=456,
            discord_message_id=555,
            target_user_id="111",
            created_by_user_id="222",
        )

        orig_msg = MagicMock()
        ping_msg1 = MagicMock()
        ping_msg1.id = 2001
        ping_msg1.delete = AsyncMock()
        ping_msg1.add_reaction = AsyncMock()

        ping_msg2 = MagicMock()
        ping_msg2.id = 2002
        ping_msg2.delete = AsyncMock()
        ping_msg2.add_reaction = AsyncMock()

        orig_msg.reply = AsyncMock(side_effect=[ping_msg1, ping_msg2])

        mock_channel = MagicMock()
        async def fetch_msg(mid):
            if mid == 555:
                return orig_msg
            if mid == 2001:
                return ping_msg1
            if mid == 2002:
                return ping_msg2
            return None
        mock_channel.fetch_message = AsyncMock(side_effect=fetch_msg)
        self.bot.get_channel = MagicMock(return_value=mock_channel)

        await self.bot.send_custom_escalation_ping(
            channel_id=456,
            message_id=555,
            stage=0,
            target_user_id="111",
            message_text="Deliver export",
        )
        pings = self.db.get_custom_reminder_pings("remind_rolling")
        self.assertEqual(len(pings), 1)
        self.assertEqual(pings[0]["ping_message_id"], 2001)

        await self.bot.send_custom_escalation_ping(
            channel_id=456,
            message_id=555,
            stage=1,
            target_user_id="111",
            message_text="Deliver export",
        )
        ping_msg1.delete.assert_called_once()
        pings_after = self.db.get_custom_reminder_pings("remind_rolling")
        self.assertEqual(len(pings_after), 1)
        self.assertEqual(pings_after[0]["ping_message_id"], 2002)

    async def test_on_raw_reaction_add_heart_on_notion_ping_stops_reminder(self):
        # test heart reaction resolves
        from unittest.mock import AsyncMock
        mock_notion = MagicMock()
        mock_notion.update_page_status = MagicMock(return_value=True)
        self.bot.notion = mock_notion

        self.db.record_notion_item(
            notion_page_id="page_heart_ack",
            category="POST",
            page_title="Gr818ers x WSS Event FPV final1",
            asset_url="https://vimeo.com/gr818",
            notion_status="Ready to Post",
            assigned_users="Elvis",
            discord_channel_id=456,
            discord_message_id=555,
        )
        self.db.record_notion_item_ping(
            ping_message_id=999777,
            tracking_id="page_heart_ack:POST",
            discord_message_id=555,
        )

        orig_msg = MagicMock()
        orig_msg.embeds = [MagicMock()]
        orig_msg.edit = AsyncMock()
        ping_msg = MagicMock()
        ping_msg.reply = AsyncMock()

        mock_channel = MagicMock()
        mock_channel.fetch_message = AsyncMock(side_effect=lambda mid: ping_msg if mid == 999777 else orig_msg)
        self.bot.get_channel = MagicMock(return_value=mock_channel)

        payload = MagicMock()
        payload.user_id = 259869751130587137
        payload.message_id = 999777
        payload.channel_id = 456
        payload.emoji = MagicMock()
        payload.emoji.name = "❤️"

        await self.bot.on_raw_reaction_add(payload)

        # verify resolved
        item = self.db.get_notion_item("page_heart_ack:POST")
        self.assertEqual(item["status"], "RESOLVED")
        mock_notion.update_page_status.assert_called_once_with("page_heart_ack", "Posted")
        ping_msg.reply.assert_called_once()
        self.assertIn("Task acknowledged", ping_msg.reply.call_args.args[0])

    async def test_on_raw_reaction_add_check_on_notion_ping_stops_reminder(self):
        # test check reaction resolves
        from unittest.mock import AsyncMock
        mock_notion = MagicMock()
        mock_notion.update_page_status = MagicMock(return_value=True)
        self.bot.notion = mock_notion

        self.db.record_notion_item(
            notion_page_id="page_check_ack",
            category="POST",
            page_title="Gr818ers x WSS Event FPV final1",
            asset_url="https://vimeo.com/gr818",
            notion_status="Ready to Post",
            assigned_users="Elvis",
            discord_channel_id=456,
            discord_message_id=555,
        )
        self.db.record_notion_item_ping(
            ping_message_id=999888,
            tracking_id="page_check_ack:POST",
            discord_message_id=555,
        )

        orig_msg = MagicMock()
        orig_msg.embeds = [MagicMock()]
        orig_msg.edit = AsyncMock()
        ping_msg = MagicMock()
        ping_msg.reply = AsyncMock()

        mock_channel = MagicMock()
        mock_channel.fetch_message = AsyncMock(side_effect=lambda mid: ping_msg if mid == 999888 else orig_msg)
        self.bot.get_channel = MagicMock(return_value=mock_channel)

        payload = MagicMock()
        payload.user_id = 259869751130587137
        payload.message_id = 999888
        payload.channel_id = 456
        payload.emoji = MagicMock()
        payload.emoji.name = "✅"

        await self.bot.on_raw_reaction_add(payload)

        # verify resolved
        item = self.db.get_notion_item("page_check_ack:POST")
        self.assertEqual(item["status"], "RESOLVED")
        mock_notion.update_page_status.assert_called_once_with("page_check_ack", "Posted")
        ping_msg.reply.assert_called_once()
        self.assertIn("Status updated to **Posted**", ping_msg.reply.call_args.args[0])

    async def test_on_message_reply_stops_notion_reminder(self):
        # test reply to ping resolves
        from unittest.mock import AsyncMock
        mock_notion = MagicMock()
        mock_notion.update_page_status = MagicMock(return_value=True)
        self.bot.notion = mock_notion

        self.db.record_notion_item(
            notion_page_id="page_reply_ack",
            category="POST",
            page_title="Gr818ers x WSS Event FPV final1",
            asset_url="https://vimeo.com/gr818",
            notion_status="Ready to Post",
            assigned_users="Elvis",
            discord_channel_id=456,
            discord_message_id=555,
        )
        self.db.record_notion_item_ping(
            ping_message_id=999999,
            tracking_id="page_reply_ack:POST",
            discord_message_id=555,
        )

        orig_msg = MagicMock()
        orig_msg.embeds = [MagicMock()]
        orig_msg.edit = AsyncMock()
        mock_channel = MagicMock()
        mock_channel.fetch_message = AsyncMock(return_value=orig_msg)
        self.bot.get_channel = MagicMock(return_value=mock_channel)

        mock_reply_msg = MagicMock()
        mock_reply_msg.author = MagicMock()
        mock_reply_msg.author.id = 259869751130587137
        mock_reply_msg.author.mention = "<@259869751130587137>"
        mock_reply_msg.content = "posted"
        mock_reply_msg.reference = MagicMock()
        mock_reply_msg.reference.message_id = 999999
        mock_reply_msg.channel = mock_channel
        mock_reply_msg.reply = AsyncMock()

        await self.bot.on_message(mock_reply_msg)

        item = self.db.get_notion_item("page_reply_ack:POST")
        self.assertEqual(item["status"], "RESOLVED")
        mock_notion.update_page_status.assert_called_once_with("page_reply_ack", "Posted")
        mock_reply_msg.reply.assert_called_once()
        self.assertIn("Task acknowledged", mock_reply_msg.reply.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
