import unittest
from unittest.mock import MagicMock
import tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta

from db import ReviewDatabase
from bot import VertigohVisionBot, TEAM_ROLE_IDS


class TestVertigohVisionBot(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
