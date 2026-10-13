import asyncio
import logging
from typing import Optional
from db import ReviewDatabase
from notion_client import NotionClient
from bot import VertigohVisionBot, HEART_EMOJIS

logger = logging.getLogger(__name__)


class ReviewScheduler:
    def __init__(
        self,
        bot: VertigohVisionBot,
        notion: NotionClient,
        db: ReviewDatabase,
        channel_id: int,
        reviewer_id: str,
        notion_interval: int = 60,
        escalation_interval: int = 30,
    ):
        self.bot = bot
        self.notion = notion
        self.db = db
        self.channel_id = channel_id
        self.reviewer_id = reviewer_id
        self.notion_interval = notion_interval
        self.escalation_interval = escalation_interval
        self._is_running = False

    async def poll_notion_once(self):
        """Polls Notion for new or updated asset links and posts immediate Discord alerts."""
        try:
            logger.debug("Checking Notion for new assets...")
            assets = self.notion.query_assets_database()

            # seed baseline on startup
            if self.db.count_tracked_assets() == 0:
                logger.info("Empty database detected. Seeding %d existing Notion assets as baseline...", len(assets))
                for item in assets:
                    self.db.seed_baseline_asset(
                        notion_page_id=item["notion_page_id"],
                        page_title=item["page_title"],
                        asset_url=item["asset_url"],
                    )
                logger.info("Baseline seeding complete. Future newly added/modified assets will trigger alerts.")
                return

            for item in assets:
                page_id = item["notion_page_id"]
                current_url = item["asset_url"]
                title = item["page_title"]
                page_url = item["page_url"]

                existing = self.db.get_asset(page_id)
                # detect new or updated
                if not existing or existing["asset_url"] != current_url:
                    logger.info("Found new asset link for '%s': %s", title, current_url)
                    msg = await self.bot.send_asset_alert(
                        channel_id=self.channel_id,
                        page_title=title,
                        asset_url=current_url,
                        page_url=page_url,
                        reviewer_id=self.reviewer_id,
                    )
                    if msg:
                        self.db.record_new_asset(
                            notion_page_id=page_id,
                            page_title=title,
                            asset_url=current_url,
                            discord_channel_id=self.channel_id,
                            discord_message_id=msg.id,
                        )
                        logger.info("Alert dispatched and recorded in SQLite for page %s (msg: %s)", page_id, msg.id)
        except Exception as e:
            logger.error("Error during Notion poll tick: %s", e)

    async def check_escalations_once(self):
        """Checks for overdue asset reviews and sends escalating pings."""
        try:
            due_items = self.db.get_due_escalations()
            for item in due_items:
                page_id = item["notion_page_id"]
                stage = item["stage"]
                channel_id = item["discord_channel_id"] or self.channel_id
                message_id = item["discord_message_id"]

                logger.info("Escalating asset %s (Stage %d overdue)", page_id, stage)
                success = await self.bot.send_escalation_ping(
                    channel_id=channel_id,
                    message_id=message_id,
                    stage=stage,
                    reviewer_id=self.reviewer_id,
                    page_title=item["page_title"],
                    asset_url=item["asset_url"],
                )
                if success:
                    updated = self.db.advance_escalation_stage(page_id)
                    logger.info("Advanced %s to stage %s (next ping at %s)", page_id, updated["stage"], updated["next_ping_at"])
        except Exception as e:
            logger.error("Error during escalation check tick: %s", e)

    async def check_custom_reminders_once(self):
        """Checks for overdue custom reminders and sends escalating pings."""
        try:
            due_items = self.db.get_due_custom_reminders()
            for item in due_items:
                rem_id = item["reminder_id"]
                stage = item["stage"]
                channel_id = item["discord_channel_id"]
                message_id = item["discord_message_id"]

                logger.info("Escalating custom reminder %s (Stage %d overdue)", rem_id, stage)
                success = await self.bot.send_custom_escalation_ping(
                    channel_id=channel_id,
                    message_id=message_id,
                    stage=stage,
                    target_user_id=item["target_user_id"],
                    message_text=item["message_text"],
                )
                if success:
                    updated = self.db.advance_custom_reminder_stage(rem_id)
                    logger.info("Advanced custom reminder %s to stage %s (next ping at %s)", rem_id, updated["stage"], updated["next_ping_at"])
        except Exception as e:
            logger.error("Error during custom reminder escalation check: %s", e)

    async def reconcile_pending_reactions_once(self):
        """Reconciles Discord reactions for pending items in case reaction occurred while offline."""
        try:
            # catch offline reactions
            due_or_pending = self.db.get_due_escalations()
            for item in due_or_pending:
                msg_id = item["discord_message_id"]
                ch_id = item["discord_channel_id"] or self.channel_id
                if not msg_id or not ch_id:
                    continue

                channel = self.bot.get_channel(ch_id) or await self.bot.fetch_channel(ch_id)
                if not channel:
                    continue

                try:
                    msg = await channel.fetch_message(msg_id)
                    for reaction in msg.reactions:
                        if reaction.emoji in HEART_EMOJIS:
                            async for user in reaction.users():
                                if self.bot.user and user.id != self.bot.user.id:
                                    logger.info("Reconciliation caught offline heart reaction by %s for asset msg %s", user.id, msg_id)
                                    self.db.mark_reviewed_by_message_id(msg_id, reviewer_id=str(user.id))
                                    await self.bot.update_reviewed_message(
                                        channel_id=ch_id,
                                        message_id=msg_id,
                                        reviewer_user=user,
                                        page_title=item["page_title"],
                                    )
                                    break
                except Exception:
                    pass

            # reconcile custom reminders
            due_custom = self.db.get_due_custom_reminders()
            for item in due_custom:
                msg_id = item["discord_message_id"]
                ch_id = item["discord_channel_id"]
                if not msg_id or not ch_id:
                    continue

                channel = self.bot.get_channel(ch_id) or await self.bot.fetch_channel(ch_id)
                if not channel:
                    continue

                try:
                    msg = await channel.fetch_message(msg_id)
                    for reaction in msg.reactions:
                        if reaction.emoji in HEART_EMOJIS:
                            async for user in reaction.users():
                                if self.bot.user and user.id != self.bot.user.id:
                                    logger.info("Reconciliation caught offline heart reaction by %s for custom reminder msg %s", user.id, msg_id)
                                    self.db.mark_custom_reminder_acknowledged(msg_id, acknowledged_by=str(user.id))
                                    await self.bot.update_acknowledged_reminder_message(
                                        channel_id=ch_id,
                                        message_id=msg_id,
                                        acknowledged_by_user=user,
                                        message_text=item["message_text"],
                                    )
                                    break
                except Exception:
                    pass
        except Exception as e:
            logger.debug("Reconciliation check failed: %s", e)

    async def start(self):
        """Starts the periodic background loops."""
        self._is_running = True
        logger.info("ReviewScheduler started (Notion poll: %ds, Escalation: %ds)", self.notion_interval, self.escalation_interval)

        async def notion_loop():
            while self._is_running:
                await self.poll_notion_once()
                await asyncio.sleep(self.notion_interval)

        async def escalation_loop():
            while self._is_running:
                await self.reconcile_pending_reactions_once()
                await self.check_escalations_once()
                await self.check_custom_reminders_once()
                await asyncio.sleep(self.escalation_interval)

        await asyncio.gather(notion_loop(), escalation_loop())

    def stop(self):
        self._is_running = False
        logger.info("ReviewScheduler stopped.")
