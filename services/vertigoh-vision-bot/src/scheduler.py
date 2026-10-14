import asyncio
import logging
from typing import Optional, List
from db import ReviewDatabase, get_current_utc
from notion_client import NotionClient
from bot import VertigohVisionBot, is_heart_emoji

logger = logging.getLogger(__name__)

REVIEW_TARGETS = {"roger", "kai", "elvis"}


class ReviewScheduler:
    def __init__(
        self,
        bot: VertigohVisionBot,
        notion: NotionClient,
        db: ReviewDatabase,
        channel_id: int,
        reviewer_id: str = "",
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
        # poll notion updates
        try:
            logger.debug("Polling Notion schedule database...")
            items = self.notion.query_assets_database()
            notion_map = {item["notion_page_id"]: item for item in items}

            # reconcile active items
            active_items = self.db.get_active_notion_items()
            for active in active_items:
                page_id = active["notion_page_id"]
                category = active["category"]
                tracking_id = active["tracking_id"]
                item = notion_map.get(page_id)

                if not item:
                    # page removed notion
                    self.db.mark_notion_item_resolved(tracking_id)
                    continue

                curr_status = item.get("status", "").strip()
                curr_asset = item.get("asset_url", "").strip()
                reviewers = item.get("reviewers", [])
                editors = item.get("editors", [])
                uploaders = item.get("uploaders", [])

                if category == "REVIEW":
                    # verify review conditions
                    is_review_pending = curr_status.lower() == "review pending"
                    has_asset = bool(curr_asset)
                    has_reviewer = any(r.lower() in REVIEW_TARGETS for r in reviewers)
                    if not (is_review_pending and has_asset and has_reviewer):
                        self.db.mark_notion_item_resolved(tracking_id)
                        await self.bot.update_resolved_message(
                            channel_id=active["discord_channel_id"] or self.channel_id,
                            message_id=active["discord_message_id"],
                            page_title=active["page_title"],
                            category="REVIEW",
                            current_status=curr_status or "Updated",
                        )
                        logger.info("Review resolved for '%s' (status: %s)", active["page_title"], curr_status)

                elif category == "EDIT":
                    # verify edit conditions
                    is_edit_pending = curr_status.lower() == "edit pending"
                    has_asset = bool(curr_asset)
                    has_editor = len(editors) > 0
                    if not (is_edit_pending and has_asset and has_editor):
                        self.db.mark_notion_item_resolved(tracking_id)
                        await self.bot.update_resolved_message(
                            channel_id=active["discord_channel_id"] or self.channel_id,
                            message_id=active["discord_message_id"],
                            page_title=active["page_title"],
                            category="EDIT",
                            current_status=curr_status or "Updated",
                        )
                        logger.info("Edit resolved for '%s' (status: %s)", active["page_title"], curr_status)

                elif category == "POST":
                    # verify post conditions
                    is_ready_to_post = curr_status.lower() == "ready to post"
                    is_scheduled = curr_status.lower() == "scheduled"
                    has_asset = bool(curr_asset)
                    has_uploader = len(uploaders) > 0
                    if is_scheduled or not (is_ready_to_post and has_asset and has_uploader):
                        self.db.mark_notion_item_resolved(tracking_id)
                        await self.bot.update_resolved_message(
                            channel_id=active["discord_channel_id"] or self.channel_id,
                            message_id=active["discord_message_id"],
                            page_title=active["page_title"],
                            category="POST",
                            current_status=curr_status or "Updated",
                        )
                        logger.info("Post resolved for '%s' (status: %s)", active["page_title"], curr_status)

            # check new alerts
            for item in items:
                page_id = item["notion_page_id"]
                title = item["page_title"]
                asset_url = item["asset_url"].strip()
                page_url = item["page_url"]
                status = item["status"].strip()
                status_lower = status.lower()
                reviewers = item["reviewers"]
                editors = item["editors"]
                uploaders = item.get("uploaders", [])

                # check review pending
                matched_reviewers = [r for r in reviewers if r.lower() in REVIEW_TARGETS]
                if status_lower == "review pending" and asset_url and matched_reviewers:
                    tracking_id = f"{page_id}:REVIEW"
                    existing = self.db.get_notion_item(tracking_id)
                    if not existing or existing["status"] != "PENDING":
                        msg = await self.bot.send_review_alert(
                            channel_id=self.channel_id,
                            page_title=title,
                            asset_url=asset_url,
                            page_url=page_url,
                            reviewers=matched_reviewers,
                        )
                        if msg:
                            self.db.record_notion_item(
                                notion_page_id=page_id,
                                category="REVIEW",
                                page_title=title,
                                asset_url=asset_url,
                                notion_status=status,
                                assigned_users=",".join(matched_reviewers),
                                discord_channel_id=self.channel_id,
                                discord_message_id=msg.id,
                                page_url=page_url,
                            )
                            logger.info("Dispatched review alert for '%s' (msg: %s)", title, msg.id)

                # check edit pending
                if status_lower == "edit pending" and asset_url and editors:
                    tracking_id = f"{page_id}:EDIT"
                    existing = self.db.get_notion_item(tracking_id)
                    if not existing or existing["status"] != "PENDING":
                        msg = await self.bot.send_edit_alert(
                            channel_id=self.channel_id,
                            page_title=title,
                            asset_url=asset_url,
                            page_url=page_url,
                            editors=editors,
                        )
                        if msg:
                            self.db.record_notion_item(
                                notion_page_id=page_id,
                                category="EDIT",
                                page_title=title,
                                asset_url=asset_url,
                                notion_status=status,
                                assigned_users=",".join(editors),
                                discord_channel_id=self.channel_id,
                                discord_message_id=msg.id,
                                page_url=page_url,
                            )
                            logger.info("Dispatched edit alert for '%s' (msg: %s)", title, msg.id)

                # check ready to post
                if status_lower == "ready to post" and asset_url and uploaders:
                    tracking_id = f"{page_id}:POST"
                    existing = self.db.get_notion_item(tracking_id)
                    if not existing or existing["status"] != "PENDING":
                        msg = await self.bot.send_ready_to_post_alert(
                            channel_id=self.channel_id,
                            page_title=title,
                            asset_url=asset_url,
                            page_url=page_url,
                            uploaders=uploaders,
                        )
                        if msg:
                            self.db.record_notion_item(
                                notion_page_id=page_id,
                                category="POST",
                                page_title=title,
                                asset_url=asset_url,
                                notion_status=status,
                                assigned_users=",".join(uploaders),
                                discord_channel_id=self.channel_id,
                                discord_message_id=msg.id,
                                page_url=page_url,
                            )
                            logger.info("Dispatched ready to post alert for '%s' (msg: %s)", title, msg.id)

        except Exception as e:
            logger.error("Error during Notion poll tick: %s", e)

    async def check_escalations_once(self):
        # check overdue escalations
        try:
            global_snooze = self.db.get_snooze_until()
            if global_snooze and get_current_utc() < global_snooze:
                logger.debug("Reminders snoozed until %s", global_snooze)
                return

            due_items = self.db.get_due_notion_items()
            for item in due_items:
                tracking_id = item["tracking_id"]
                stage = item["stage"]
                channel_id = item["discord_channel_id"] or self.channel_id
                message_id = item["discord_message_id"]
                category = item["category"]
                assigned = [u.strip() for u in item["assigned_users"].split(",") if u.strip()]

                channel = self.bot.get_channel(channel_id)
                guild = channel.guild if channel and hasattr(channel, "guild") else None

                # filter out snoozed recipients
                active_targets = [u for u in assigned if not self.bot.is_target_snoozed(u, guild)]

                if not active_targets:
                    logger.debug("All assigned targets for %s are snoozed, skipping ping", tracking_id)
                    continue

                logger.info("Escalating %s (%s stage %d to %s)", tracking_id, category, stage, active_targets)
                success = await self.bot.send_notion_escalation(
                    channel_id=channel_id,
                    message_id=message_id,
                    stage=stage,
                    category=category,
                    page_title=item["page_title"],
                    asset_url=item["asset_url"],
                    page_url=item["page_url"],
                    assigned_users=active_targets,
                )
                if success:
                    updated = self.db.advance_notion_item_stage(tracking_id)
                    logger.info("Advanced %s to stage %s (next: %s)", tracking_id, updated["stage"], updated["next_ping_at"])

        except Exception as e:
            logger.error("Error during escalation check tick: %s", e)

    async def check_custom_reminders_once(self):
        # check custom reminders
        try:
            snooze_until = self.db.get_snooze_until()
            if snooze_until and get_current_utc() < snooze_until:
                return

            due_items = self.db.get_due_custom_reminders()
            for item in due_items:
                rem_id = item["reminder_id"]
                stage = item["stage"]
                channel_id = item["discord_channel_id"]
                message_id = item["discord_message_id"]

                success = await self.bot.send_custom_escalation_ping(
                    channel_id=channel_id,
                    message_id=message_id,
                    stage=stage,
                    target_user_id=item["target_user_id"],
                    message_text=item["message_text"],
                )
                if success:
                    self.db.advance_custom_reminder_stage(rem_id)

        except Exception as e:
            logger.error("Error during custom reminder check: %s", e)

    async def reconcile_custom_reminders_once(self):
        # catch offline reactions
        try:
            pending_items = self.db.get_all_pending_custom_reminders()
            for item in pending_items:
                ch_id = item["discord_channel_id"]
                msg_id = item["discord_message_id"]
                channel = self.bot.get_channel(ch_id) or await self.bot.fetch_channel(ch_id)
                if not channel:
                    continue

                messages_to_check = [msg_id]
                pings = self.db.get_custom_reminder_pings(item["reminder_id"])
                messages_to_check.extend([p["ping_message_id"] for p in pings])

                acked = False
                for mid in messages_to_check:
                    if acked:
                        break
                    try:
                        m = await channel.fetch_message(mid)
                        for reaction in m.reactions:
                            emoji_str = str(reaction.emoji.name) if reaction.emoji and hasattr(reaction.emoji, "name") and reaction.emoji.name else str(reaction.emoji)
                            if is_heart_emoji(emoji_str):
                                async for user in reaction.users():
                                    if self.bot.user and user.id != self.bot.user.id:
                                        logger.info("Caught offline heart reaction by %s on %s", user.id, mid)
                                        self.db.mark_custom_reminder_acknowledged(
                                            discord_message_id=item["discord_message_id"],
                                            acknowledged_by=str(user.id),
                                        )
                                        await self.bot.update_acknowledged_reminder_message(
                                            channel_id=ch_id,
                                            message_id=item["discord_message_id"],
                                            acknowledged_by_user=user,
                                            message_text=item["message_text"],
                                            reacted_message_id=mid,
                                        )
                                        acked = True
                                        break
                    except Exception:
                        pass
        except Exception as e:
            logger.debug("Offline reaction reconciliation failed: %s", e)

    async def start(self):
        # start background loops
        self._is_running = True
        logger.info("ReviewScheduler started (Notion poll: %ds, Escalation: %ds)", self.notion_interval, self.escalation_interval)

        async def notion_loop():
            while self._is_running:
                await self.poll_notion_once()
                await asyncio.sleep(self.notion_interval)

        async def escalation_loop():
            while self._is_running:
                await self.reconcile_custom_reminders_once()
                await self.check_escalations_once()
                await self.check_custom_reminders_once()
                await asyncio.sleep(self.escalation_interval)

        await asyncio.gather(notion_loop(), escalation_loop())

    def stop(self):
        # stop background loops
        self._is_running = False
        logger.info("ReviewScheduler stopped.")
