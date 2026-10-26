import logging
import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone, timedelta
from db import ReviewDatabase, get_current_utc

logger = logging.getLogger(__name__)

TEAM_ROLE_IDS = {
    "kai": 1559491322157600850,
    "roger": 1559491699775115274,
    "elvis": 1559491606653173760,
}

TEAM_ROLE_TO_USERS = {
    "kai": {"264314531818242049"},
    "roger": {"221159271738376192"},
    "elvis": {"259869751130587137"},
}

ALERT_REACTION_EMOJIS = ["👀", "🎬", "↗️", "✅"]

EMOJI_TO_STATUS = {
    # review pending
    "👀": "Review Pending",
    "eyes": "Review Pending",
    # edit pending
    "🎬": "Edit Pending",
    "clapper": "Edit Pending",
    # ready to post
    "↗️": "Ready to Post",
    "↗": "Ready to Post",
    "\u2197\ufe0f": "Ready to Post",
    "\u2197": "Ready to Post",
    "arrow_upper_right": "Ready to Post",
    # posted
    "✅": "Posted",
    "white_check_mark": "Posted",
}


def format_status(status_str: str, is_resolved: bool = False) -> str:
    # format status with emoji
    clean = status_str.strip()
    for prefix in ("✅ ", "⏳ "):
        if clean.startswith(prefix):
            clean = clean[len(prefix):].strip()

    clean_lower = clean.lower()
    if is_resolved:
        if clean_lower in ("scheduled", "posted", "done", "completed"):
            return f"✅ {clean}"
        if clean_lower.startswith("resolved in notion"):
            inner = clean[len("resolved in notion"):].strip(" ()")
            return f"✅ Resolved ({inner})" if inner else "✅ Resolved"
        if clean_lower.startswith("resolved (") and clean.endswith(")"):
            return f"✅ {clean}"
        return f"✅ Resolved ({clean})"
    else:
        return f"⏳ {clean}"


HEART_EMOJIS = {
    "❤️", "❤", "\u2764\ufe0f", "\u2764",
    "♥️", "♥", "\u2665\ufe0f", "\u2665",
    "💖", "💗", "💓", "💕", "💞", "💘",
    "💙", "💚", "💛", "💜", "🖤", "🤍", "🤎", "🧡", "🩵", "🩶", "🩷",
}

HEART_EMOJI_NAMES = {
    "heart", "hearts", "sparkling_heart", "heartpulse", "heartbeat",
    "two_hearts", "revolving_hearts", "cupid", "gift_heart",
    "blue_heart", "green_heart", "yellow_heart", "purple_heart",
    "black_heart", "white_heart", "brown_heart", "orange_heart",
    "pink_heart", "grey_heart", "light_blue_heart",
}


def is_heart_emoji(emoji_val: str) -> bool:
    # check heart emoji
    clean = emoji_val.strip()
    if clean in HEART_EMOJIS:
        return True
    name = clean.strip(":").lower()
    if name in HEART_EMOJI_NAMES or "heart" in name:
        return True
    return False


class VertigohVisionBot(commands.Bot):
    def __init__(
        self,
        db: ReviewDatabase,
        reviewer_id: str,
        guild_id: Optional[int] = None,
        notion: Optional[Any] = None,
        *args,
        **kwargs,
    ):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.reactions = True
        super().__init__(command_prefix="!", intents=intents, *args, **kwargs)
        self.db = db
        self.reviewer_id = reviewer_id
        self.guild_id = guild_id
        self.notion = notion
        self.scheduler = None
        self._register_commands()

    def _register_commands(self):
        # register slash commands

        @self.tree.command(name="snooze", description="Snooze your reminder pings for 12 hours")
        async def snooze_slash(interaction: discord.Interaction):
            until = get_current_utc() + timedelta(hours=12)
            self.db.set_user_snooze(str(interaction.user.id), until)
            unix_ts = int(until.timestamp())
            await interaction.response.send_message(
                f"💤 {interaction.user.mention}, you have snoozed your reminder pings for **12 hours** (until <t:{unix_ts}:f>).",
                ephemeral=False,
            )

        @self.tree.command(name="unsnooze", description="Cancel your snooze and resume reminder pings immediately")
        async def unsnooze_slash(interaction: discord.Interaction):
            self.db.clear_user_snooze(str(interaction.user.id))
            await interaction.response.send_message(
                f"⏰ {interaction.user.mention}, your snooze has been cancelled. Reminders resumed.",
                ephemeral=False,
            )

        @self.tree.command(name="status", description="Show active reminders and your snooze status")
        async def status_slash(interaction: discord.Interaction):
            user_snooze = self.db.get_user_snooze(str(interaction.user.id))
            snooze_txt = f"Active until <t:{int(user_snooze.timestamp())}:f>" if user_snooze else "None (Active)"
            reviews = self.db.get_active_notion_items("REVIEW")
            edits = self.db.get_active_notion_items("EDIT")
            posts = self.db.get_active_notion_items("POST")
            embed = discord.Embed(title="🤖 Vertigoh Vision Bot Status", color=0x3498DB)
            embed.add_field(name="💤 Your Snooze", value=snooze_txt, inline=False)
            embed.add_field(name="🎬 Review Pending", value=f"{len(reviews)} active items", inline=True)
            embed.add_field(name="✂️ Edit Pending", value=f"{len(edits)} active items", inline=True)
            embed.add_field(name="🚀 Ready to Post", value=f"{len(posts)} active items", inline=True)
            await interaction.response.send_message(embed=embed, ephemeral=True)

        @self.tree.command(
            name="remind",
            description="Set an escalating reminder for a team member",
        )
        @app_commands.describe(
            target="The team member to remind (e.g. @kaivertigoh)",
            message="The reminder message or task details",
        )
        async def remind_slash(interaction: discord.Interaction, target: discord.Member, message: str):
            await interaction.response.defer(ephemeral=False)
            sent_msg = await self.create_custom_reminder(
                channel=interaction.channel,
                target_user=target,
                message_text=message,
                author_user=interaction.user,
            )
            if sent_msg:
                await interaction.followup.send(
                    f"✅ Reminder scheduled for {target.mention}!",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    "❌ Failed to dispatch reminder. Check channel permissions.",
                    ephemeral=True,
                )

        @self.tree.command(
            name="purge",
            description="Clean old bot messages in this channel while preserving active reminder cards",
        )
        @app_commands.describe(
            limit="Maximum number of messages to scan (default: 100, max: 500)",
        )
        async def purge_slash(interaction: discord.Interaction, limit: Optional[int] = 100):
            await interaction.response.defer(ephemeral=True)
            scan_limit = min(max(limit or 100, 1), 500)
            purged, preserved = await self.purge_channel_messages(interaction.channel, limit=scan_limit)
            await interaction.followup.send(
                f"🧹 **Purged {purged} old bot messages!**\n"
                f"✅ Preserved {preserved} active reminder message(s).",
                ephemeral=True,
            )

        @self.tree.command(
            name="nuke",
            description="Nuke all bot alert messages in this channel and resync fresh cards from Notion",
        )
        @app_commands.describe(
            limit="Maximum number of messages to scan (default: 200, max: 500)",
        )
        async def nuke_slash(interaction: discord.Interaction, limit: Optional[int] = 200):
            await interaction.response.defer(ephemeral=True)
            scan_limit = min(max(limit or 200, 1), 500)
            nuked = await self.nuke_channel_messages(interaction.channel, limit=scan_limit)
            await interaction.followup.send(
                f"💥 **Nuked {nuked} bot messages!**\n"
                f"🔄 Cache cleared and active Notion schedule cards resynced.",
                ephemeral=True,
            )

    async def setup_hook(self):
        # sync commands to guild
        if self.guild_id:
            guild_obj = discord.Object(id=self.guild_id)
            self.tree.copy_global_to(guild=guild_obj)
            await self.tree.sync(guild=guild_obj)
            logger.info("Synchronized application slash commands to guild %s", self.guild_id)
        else:
            await self.tree.sync()
            logger.info("Synchronized global application slash commands")

    async def sync_all_guild_members(self):
        # sync guild members
        logger.info("Scanning Discord guilds for members...")
        for guild in self.guilds:
            try:
                count = 0
                async for member in guild.fetch_members(limit=None):
                    roles = ",".join(r.name for r in member.roles if r.name != "@everyone")
                    self.db.upsert_discord_user(
                        user_id=str(member.id),
                        username=member.name,
                        display_name=member.display_name,
                        is_bot=member.bot,
                        roles=roles,
                    )
                    count += 1
                logger.info("Synchronized %d members from guild '%s' (ID: %s)", count, guild.name, guild.id)
            except Exception as e:
                logger.warning("Could not sync members for guild '%s': %s", guild.name, e)

    async def on_ready(self):
        # bot connected ready
        logger.info("Logged in as Discord Bot: %s (ID: %s)", self.user.name, self.user.id)
        await self.sync_all_guild_members()

    def resolve_reviewer_id(self, reviewer_ref: Optional[str] = None) -> str:
        # resolve user id
        ref = reviewer_ref or self.reviewer_id
        if not ref:
            return ""
        if ref.isdigit():
            return ref
        user_match = self.db.find_user_by_name(ref)
        if user_match:
            return user_match["user_id"]
        return ref

    def resolve_role_mentions(self, names: List[str], guild: Optional[discord.Guild] = None) -> str:
        # resolve names to role mentions
        mentions = []
        for name in names:
            clean = name.strip()
            lower = clean.lower()
            if lower in TEAM_ROLE_IDS:
                mentions.append(f"<@&{TEAM_ROLE_IDS[lower]}>")
            elif guild:
                role = discord.utils.find(lambda r: r.name.lower() == lower, guild.roles)
                if role:
                    mentions.append(f"<@&{role.id}>")
                else:
                    mentions.append(f"@{clean}")
            else:
                mentions.append(f"@{clean}")
        return " ".join(mentions) if mentions else "@everyone"

    resolve_mentions = resolve_role_mentions

    def is_target_snoozed(self, name: str, guild: Optional[discord.Guild] = None) -> bool:
        # check if recipient snoozed
        lower = name.strip().lower()
        if lower in TEAM_ROLE_TO_USERS:
            for uid in TEAM_ROLE_TO_USERS[lower]:
                if self.db.is_user_snoozed(uid):
                    return True

        if guild:
            role = discord.utils.find(lambda r: r.name.lower() == lower, guild.roles)
            if role:
                for member in role.members:
                    if self.db.is_user_snoozed(str(member.id)):
                        return True

        u = self.db.find_user_by_name(name)
        if u and self.db.is_user_snoozed(u["user_id"]):
            return True

        return False

    async def _get_destination_channel(self, channel_id: int) -> Optional[discord.TextChannel]:
        # get text channel
        channel = self.get_channel(channel_id)
        if not channel:
            try:
                channel = await self.fetch_channel(channel_id)
            except Exception as e:
                logger.error("Failed to fetch channel %s: %s", channel_id, e)
                return None
        return channel

    def resolve_status_from_emoji(self, emoji_val: str) -> Optional[str]:
        # resolve emoji string to status
        clean = emoji_val.strip()
        if clean in EMOJI_TO_STATUS:
            return EMOJI_TO_STATUS[clean]
        no_colon = clean.strip(":")
        if no_colon in EMOJI_TO_STATUS:
            return EMOJI_TO_STATUS[no_colon]
        return None

    async def apply_status_update(self, page_id: str, new_status: str):
        # update notion status and reconcile
        if not self.notion:
            return False

        success = self.notion.update_page_status(page_id, new_status)
        if success and self.scheduler:
            await self.scheduler.poll_notion_once()
        return success

    async def _find_custom_reminder_from_message(
        self, channel_id: int, message_id: int
    ) -> Optional[Dict[str, Any]]:
        # find reminder from message
        try:
            channel = await self._get_destination_channel(channel_id)
            if not channel:
                return None
            msg = await channel.fetch_message(message_id)
            if not msg:
                return None

            if msg.reference and msg.reference.message_id:
                rem = self.db.get_custom_reminder_by_any_message_id(msg.reference.message_id)
                if rem:
                    return rem

            if "Reminder" in msg.content:
                pending = self.db.get_all_pending_custom_reminders()
                for r in pending:
                    if r["discord_channel_id"] == channel_id and r["message_text"] in msg.content:
                        return r
        except Exception as e:
            logger.debug("Failed reminder resolution: %s", e)
        return None

    async def _find_notion_item_from_message(
        self, channel_id: int, message_id: int
    ) -> Optional[Dict[str, Any]]:
        # find notion item from message
        try:
            channel = await self._get_destination_channel(channel_id)
            if not channel:
                return None
            msg = await channel.fetch_message(message_id)
            if not msg:
                return None

            # check reference
            if msg.reference and msg.reference.message_id:
                item = self.db.get_notion_item_by_any_message_id(msg.reference.message_id)
                if item:
                    return item

            # match active items
            active = self.db.get_active_notion_items()
            content_lower = msg.content.lower().replace("-", "")
            for item in active:
                clean_pid = item["notion_page_id"].replace("-", "").lower()
                clean_title = item["page_title"].strip("“\"'").lower()
                if (clean_pid and clean_pid in content_lower) or (clean_title and clean_title in msg.content.lower()):
                    self.db.record_notion_item_ping(
                        ping_message_id=message_id,
                        tracking_id=item["tracking_id"],
                        discord_message_id=item["discord_message_id"],
                    )
                    return item
        except Exception as e:
            logger.debug("Failed notion resolution: %s", e)
        return None

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        # handle reaction to alert message
        if self.user and payload.user_id == self.user.id:
            return

        emoji_str = str(payload.emoji.name) if payload.emoji.name else str(payload.emoji)

        # check heart reaction
        if is_heart_emoji(emoji_str):
            custom_rem = self.db.get_custom_reminder_by_any_message_id(payload.message_id)
            if not custom_rem:
                custom_rem = await self._find_custom_reminder_from_message(
                    channel_id=payload.channel_id,
                    message_id=payload.message_id,
                )

            if custom_rem and custom_rem["status"] == "PENDING":
                logger.info(
                    "Heart reaction '%s' by %s on message %d: stopping reminder %s",
                    emoji_str,
                    payload.user_id,
                    payload.message_id,
                    custom_rem["reminder_id"],
                )
                self.db.mark_custom_reminder_acknowledged(
                    discord_message_id=custom_rem["discord_message_id"],
                    acknowledged_by=str(payload.user_id),
                )
                user = self.get_user(payload.user_id)
                if not user:
                    try:
                        user = await self.fetch_user(payload.user_id)
                    except Exception:
                        user = None
                await self.update_acknowledged_reminder_message(
                    channel_id=custom_rem["discord_channel_id"],
                    message_id=custom_rem["discord_message_id"],
                    acknowledged_by_user=user,
                    message_text=custom_rem["message_text"],
                    reacted_message_id=payload.message_id,
                )
                return

            notion_item = self.db.get_notion_item_by_any_message_id(payload.message_id)
            if not notion_item:
                notion_item = await self._find_notion_item_from_message(
                    channel_id=payload.channel_id,
                    message_id=payload.message_id,
                )

            if notion_item and notion_item["status"] == "PENDING":
                cat = notion_item["category"].upper()
                target_status = "Posted" if cat in ("POST", "READY_TO_POST") else ("Reviewed" if cat == "REVIEW" else "Ready to Post")
                logger.info(
                    "Heart reaction '%s' by %s on message %d: stopping notion reminder %s",
                    emoji_str,
                    payload.user_id,
                    payload.message_id,
                    notion_item["tracking_id"],
                )
                await self.apply_status_update(notion_item["notion_page_id"], target_status)
                self.db.mark_notion_item_resolved(notion_item["tracking_id"])
                await self.update_resolved_message(
                    channel_id=notion_item["discord_channel_id"] or payload.channel_id,
                    message_id=notion_item["discord_message_id"],
                    page_title=notion_item["page_title"],
                    category=notion_item["category"],
                    current_status=target_status,
                )
                if payload.message_id != notion_item["discord_message_id"]:
                    try:
                        user = self.get_user(payload.user_id)
                        if not user:
                            try:
                                user = await self.fetch_user(payload.user_id)
                            except Exception:
                                user = None
                        mention = user.mention if user else f"<@{payload.user_id}>"
                        channel = await self._get_destination_channel(payload.channel_id)
                        if channel:
                            target_msg = await channel.fetch_message(payload.message_id)
                            await target_msg.reply(
                                f"✅ Task acknowledged by {mention}! Reminder pings cleared."
                            )
                    except Exception:
                        pass
                return

        target_status = self.resolve_status_from_emoji(emoji_str)
        if not target_status:
            return

        item = self.db.get_notion_item_by_any_message_id(payload.message_id)
        if not item:
            item = await self._find_notion_item_from_message(
                channel_id=payload.channel_id,
                message_id=payload.message_id,
            )
        if not item:
            return

        logger.info(
            "Reaction '%s' on message %d: updating Notion page %s status to '%s'",
            emoji_str,
            payload.message_id,
            item["notion_page_id"],
            target_status,
        )
        await self.apply_status_update(item["notion_page_id"], target_status)
        cat = item["category"].upper()
        is_resolved = False
        if cat in ("POST", "READY_TO_POST") and target_status in ("Posted", "Scheduled"):
            is_resolved = True
        elif cat == "REVIEW" and target_status != "Review Pending":
            is_resolved = True
        elif cat == "EDIT" and target_status not in ("Edit Pending", "Review Pending"):
            is_resolved = True

        if is_resolved:
            self.db.mark_notion_item_resolved(item["tracking_id"])
            await self.update_resolved_message(
                channel_id=item["discord_channel_id"] or payload.channel_id,
                message_id=item["discord_message_id"],
                page_title=item["page_title"],
                category=item["category"],
                current_status=target_status,
            )

        if payload.message_id != item["discord_message_id"]:
            try:
                user = self.get_user(payload.user_id)
                if not user:
                    try:
                        user = await self.fetch_user(payload.user_id)
                    except Exception:
                        user = None
                mention = user.mention if user else f"<@{payload.user_id}>"
                channel = await self._get_destination_channel(payload.channel_id)
                if channel:
                    target_msg = await channel.fetch_message(payload.message_id)
                    await target_msg.reply(
                        f"✅ Status updated to **{target_status}** by {mention}! Reminder pings cleared."
                    )
            except Exception:
                pass

    async def on_message(self, message: discord.Message):
        # handle reply with emoji
        if self.user and message.author.id == self.user.id:
            return

        if message.reference and message.reference.message_id:
            cleaned = message.content.strip()

            # check heart reply
            if is_heart_emoji(cleaned) or cleaned.lower() in ("done", "stop", "acknowledged", "posted", "scheduled"):
                custom_rem = self.db.get_custom_reminder_by_any_message_id(message.reference.message_id)
                if not custom_rem:
                    custom_rem = await self._find_custom_reminder_from_message(
                        channel_id=message.channel.id,
                        message_id=message.reference.message_id,
                    )
                if custom_rem and custom_rem["status"] == "PENDING":
                    logger.info(
                        "Reply '%s' on message %d: stopping reminder %s",
                        cleaned,
                        message.reference.message_id,
                        custom_rem["reminder_id"],
                    )
                    self.db.mark_custom_reminder_acknowledged(
                        discord_message_id=custom_rem["discord_message_id"],
                        acknowledged_by=str(message.author.id),
                    )
                    await self.update_acknowledged_reminder_message(
                        channel_id=custom_rem["discord_channel_id"],
                        message_id=custom_rem["discord_message_id"],
                        acknowledged_by_user=message.author,
                        message_text=custom_rem["message_text"],
                        reacted_message_id=message.reference.message_id,
                    )
                    return

                notion_item = self.db.get_notion_item_by_any_message_id(message.reference.message_id)
                if not notion_item:
                    notion_item = await self._find_notion_item_from_message(
                        channel_id=message.channel.id,
                        message_id=message.reference.message_id,
                    )
                if notion_item and notion_item["status"] == "PENDING":
                    cat = notion_item["category"].upper()
                    clean_lower = cleaned.lower()
                    if clean_lower == "scheduled":
                        target_status = "Scheduled"
                    elif clean_lower in ("posted", "done"):
                        target_status = "Posted"
                    elif cat in ("POST", "READY_TO_POST"):
                        target_status = "Posted"
                    elif cat == "REVIEW":
                        target_status = "Reviewed"
                    elif cat == "EDIT":
                        target_status = "Ready to Post"
                    else:
                        target_status = "Resolved"

                    logger.info(
                        "Reply '%s' on message %d: acknowledging Notion item %s with status '%s'",
                        cleaned,
                        message.reference.message_id,
                        notion_item["tracking_id"],
                        target_status,
                    )
                    await self.apply_status_update(notion_item["notion_page_id"], target_status)
                    self.db.mark_notion_item_resolved(notion_item["tracking_id"])
                    await self.update_resolved_message(
                        channel_id=notion_item["discord_channel_id"] or message.channel.id,
                        message_id=notion_item["discord_message_id"],
                        page_title=notion_item["page_title"],
                        category=notion_item["category"],
                        current_status=target_status,
                    )
                    try:
                        await message.reply(
                            f"✅ Task acknowledged by {message.author.mention}! Reminder pings cleared."
                        )
                    except Exception:
                        pass
                    return

            target_status = self.resolve_status_from_emoji(cleaned)
            if target_status:
                item = self.db.get_notion_item_by_any_message_id(message.reference.message_id)
                if not item:
                    item = await self._find_notion_item_from_message(
                        channel_id=message.channel.id,
                        message_id=message.reference.message_id,
                    )
                if item:
                    logger.info(
                        "Reply '%s' on message %d: updating Notion page %s status to '%s'",
                        cleaned,
                        message.reference.message_id,
                        item["notion_page_id"],
                        target_status,
                    )
                    await self.apply_status_update(item["notion_page_id"], target_status)
                    cat = item["category"].upper()
                    is_resolved = False
                    if cat in ("POST", "READY_TO_POST") and target_status in ("Posted", "Scheduled"):
                        is_resolved = True
                    elif cat == "REVIEW" and target_status != "Review Pending":
                        is_resolved = True
                    elif cat == "EDIT" and target_status not in ("Edit Pending", "Review Pending"):
                        is_resolved = True

                    if is_resolved:
                        self.db.mark_notion_item_resolved(item["tracking_id"])
                        await self.update_resolved_message(
                            channel_id=item["discord_channel_id"] or message.channel.id,
                            message_id=item["discord_message_id"],
                            page_title=item["page_title"],
                            category=item["category"],
                            current_status=target_status,
                        )
                    try:
                        await message.reply(
                            f"✅ Status updated to **{target_status}** by {message.author.mention}! Reminder pings cleared."
                        )
                    except Exception:
                        pass

        await self.process_commands(message)

    async def add_alert_reactions(self, msg: discord.Message):
        # pre-add status action reactions
        for emoji in ALERT_REACTION_EMOJIS:
            try:
                await msg.add_reaction(emoji)
            except Exception as e:
                logger.warning("Failed to add reaction %s to message: %s", emoji, e)

    # notion review alert

    async def send_review_alert(
        self,
        channel_id: int,
        page_title: str,
        asset_url: str,
        page_url: str,
        reviewers: List[str],
    ) -> Optional[discord.Message]:
        # post review pending alert
        channel = await self._get_destination_channel(channel_id)
        if not channel:
            return None

        guild = channel.guild if hasattr(channel, "guild") else None
        mentions_str = self.resolve_role_mentions(reviewers, guild)
        clean_title = page_title.strip()
        display_title = clean_title if clean_title.startswith("“") or clean_title.startswith('"') else f"“{clean_title}”"

        embed = discord.Embed(
            title=display_title,
            url=asset_url,
            description=(
                f"Required Action: REVIEW\n\n"
                f"[Content Schedule]({page_url})"
            ),
            color=0xF1C40F,
        )
        embed.add_field(name="Reviewer", value=mentions_str, inline=True)
        embed.add_field(name="Status", value=format_status("Review Pending"), inline=True)
        embed.set_footer(text="Next remind in 12h")

        try:
            msg = await channel.send(
                content=f"{mentions_str} — {display_title} is ready for review",
                embed=embed,
                allowed_mentions=discord.AllowedMentions(roles=True, users=True),
            )
            await self.add_alert_reactions(msg)
            return msg
        except Exception as e:
            logger.error("Failed to send review alert for %s: %s", page_title, e)
            return None

    # notion edit alert

    async def send_edit_alert(
        self,
        channel_id: int,
        page_title: str,
        asset_url: str,
        page_url: str,
        editors: List[str],
    ) -> Optional[discord.Message]:
        # post edit pending alert
        channel = await self._get_destination_channel(channel_id)
        if not channel:
            return None

        guild = channel.guild if hasattr(channel, "guild") else None
        mentions_str = self.resolve_role_mentions(editors, guild)
        clean_title = page_title.strip()
        display_title = clean_title if clean_title.startswith("“") or clean_title.startswith('"') else f"“{clean_title}”"

        embed = discord.Embed(
            title=display_title,
            url=asset_url,
            description=(
                f"Required Action: EDIT\n\n"
                f"[Content Schedule]({page_url})"
            ),
            color=0x3498DB,
        )
        embed.add_field(name="Editor", value=mentions_str, inline=True)
        embed.add_field(name="Status", value=format_status("Edit Pending"), inline=True)
        embed.set_footer(text="One-time alert")

        try:
            msg = await channel.send(
                content=f"{mentions_str} — {display_title} is ready for edits",
                embed=embed,
                allowed_mentions=discord.AllowedMentions(roles=True, users=True),
            )
            await self.add_alert_reactions(msg)
            return msg
        except Exception as e:
            logger.error("Failed to send edit alert for %s: %s", page_title, e)
            return None

    # notion ready to post alert

    async def send_ready_to_post_alert(
        self,
        channel_id: int,
        page_title: str,
        asset_url: str,
        page_url: str,
        uploaders: List[str],
    ) -> Optional[discord.Message]:
        # post ready to post alert
        channel = await self._get_destination_channel(channel_id)
        if not channel:
            return None

        guild = channel.guild if hasattr(channel, "guild") else None
        mentions_str = self.resolve_role_mentions(uploaders, guild)
        clean_title = page_title.strip()
        display_title = clean_title if clean_title.startswith("“") or clean_title.startswith('"') else f"“{clean_title}”"

        embed = discord.Embed(
            title=display_title,
            url=asset_url,
            description=(
                f"Required Action: POST\n\n"
                f"[Content Schedule]({page_url})"
            ),
            color=0x9B59B6,
        )
        embed.add_field(name="Poster", value=mentions_str, inline=True)
        embed.add_field(name="Status", value=format_status("Ready to Post"), inline=True)
        embed.set_footer(text="Next remind in 12h")

        try:
            msg = await channel.send(
                content=f"{mentions_str} — {display_title} is ready to post",
                embed=embed,
                allowed_mentions=discord.AllowedMentions(roles=True, users=True),
            )
            await self.add_alert_reactions(msg)
            return msg
        except Exception as e:
            logger.error("Failed to send ready to post alert for %s: %s", page_title, e)
            return None

    # notion escalation pings

    async def send_notion_escalation(
        self,
        channel_id: int,
        message_id: int,
        stage: int,
        category: str,
        page_title: str,
        asset_url: str,
        page_url: str,
        assigned_users: List[str],
        notion_page_id: Optional[str] = None,
    ) -> bool:
        # send reminder ping
        channel = await self._get_destination_channel(channel_id)
        if not channel:
            return False

        guild = channel.guild if hasattr(channel, "guild") else None
        mentions_str = self.resolve_role_mentions(assigned_users, guild)
        cat_upper = category.upper()

        if cat_upper == "EDIT":
            content = f"⏰ {mentions_str} Reminder #{stage + 1} — Edit pending"
        elif cat_upper in ("POST", "READY_TO_POST"):
            content = f"🚀 {mentions_str} Reminder #{stage + 1} — Ready to post"
        else:
            content = f"⚠️ {mentions_str} Reminder #{stage + 1} — Review pending"
        next_h = "12h"

        tracking_id = None
        if notion_page_id:
            tracking_id = f"{notion_page_id}:{cat_upper}"
        else:
            existing_item = self.db.get_notion_item_by_message_id(message_id)
            if existing_item:
                tracking_id = existing_item["tracking_id"]

        prev_pings = self.db.get_notion_item_pings(tracking_id) if tracking_id else []

        try:
            ping_msg = None
            try:
                orig_msg = await channel.fetch_message(message_id)
                ping_msg = await orig_msg.reply(content=content, allowed_mentions=discord.AllowedMentions(roles=True, users=True))
                # update original embed footer
                if orig_msg.embeds:
                    embed = orig_msg.embeds[0]
                    embed.set_footer(text=f"Next remind in {next_h}")
                    await orig_msg.edit(embed=embed)
            except Exception:
                ping_msg = await channel.send(content=content, allowed_mentions=discord.AllowedMentions(roles=True, users=True))

            if ping_msg:
                # delete previous pings
                for prev in prev_pings:
                    prev_id = prev.get("ping_message_id")
                    if prev_id and prev_id != ping_msg.id:
                        try:
                            old_msg = await channel.fetch_message(prev_id)
                            await old_msg.delete()
                        except Exception:
                            pass
                        self.db.delete_notion_item_ping(prev_id)

                # add acknowledgment reactions
                if cat_upper in ("POST", "READY_TO_POST"):
                    reactions_to_add = ["✅", "❤️"]
                elif cat_upper == "EDIT":
                    reactions_to_add = ["🎬", "↗️", "✅", "❤️"]
                else:
                    reactions_to_add = ["👀", "🎬", "↗️", "✅", "❤️"]

                for r_emoji in reactions_to_add:
                    try:
                        await ping_msg.add_reaction(r_emoji)
                    except Exception as e:
                        logger.warning("Could not add reaction %s to escalation ping: %s", r_emoji, e)

                if tracking_id:
                    self.db.record_notion_item_ping(
                        ping_message_id=ping_msg.id,
                        tracking_id=tracking_id,
                        discord_message_id=message_id,
                    )
            return True
        except Exception as e:
            logger.error("Failed to send escalation ping: %s", e)
            return False

    # notion resolution update

    async def update_resolved_message(
        self,
        channel_id: int,
        message_id: int,
        page_title: str,
        category: str,
        current_status: str,
    ):
        # update embed when resolved
        try:
            channel = await self._get_destination_channel(channel_id)
            if not channel:
                return

            orig_msg = await channel.fetch_message(message_id)
            if orig_msg and orig_msg.embeds:
                old_embed = orig_msg.embeds[0]
                clean_title = page_title.strip()
                display_title = clean_title if clean_title.startswith("“") or clean_title.startswith('"') else f"“{clean_title}”"

                new_embed = discord.Embed(
                    title=display_title,
                    url=old_embed.url,
                    description=old_embed.description,
                    color=0x2ECC71,
                )
                for field in old_embed.fields:
                    if field.name.lower() in ("status", "⏳ status", "✅ status"):
                        new_embed.add_field(
                            name="Status",
                            value=format_status(current_status, is_resolved=True),
                            inline=field.inline,
                        )
                    else:
                        new_embed.add_field(name=field.name, value=field.value, inline=field.inline)
                new_embed.set_footer(text="Resolved via Notion update • Reminders stopped")
                await orig_msg.edit(embed=new_embed)
        except Exception as e:
            logger.warning("Could not update resolved message %s: %s", message_id, e)

    # backward compatibility wrappers

    async def send_asset_alert(
        self,
        channel_id: int,
        page_title: str,
        asset_url: str,
        page_url: str,
        reviewer_id: Optional[str] = None,
    ) -> Optional[discord.Message]:
        return await self.send_review_alert(
            channel_id=channel_id,
            page_title=page_title,
            asset_url=asset_url,
            page_url=page_url,
            reviewers=[reviewer_id] if reviewer_id else ["Reviewer"],
        )

    async def send_escalation_ping(
        self,
        channel_id: int,
        message_id: int,
        stage: int,
        reviewer_id: str,
        page_title: str,
        asset_url: str,
    ) -> bool:
        return await self.send_notion_escalation(
            channel_id=channel_id,
            message_id=message_id,
            stage=stage,
            category="REVIEW",
            page_title=page_title,
            asset_url=asset_url,
            page_url="",
            assigned_users=[reviewer_id] if reviewer_id else ["Reviewer"],
        )

    async def update_reviewed_message(
        self,
        channel_id: int,
        message_id: int,
        reviewer_user: Any,
        page_title: str,
    ):
        await self.update_resolved_message(
            channel_id=channel_id,
            message_id=message_id,
            page_title=page_title,
            category="REVIEW",
            current_status="Reviewed",
        )

    # custom reminders

    async def create_custom_reminder(
        self,
        channel: discord.TextChannel,
        target_user: discord.Member | discord.User,
        message_text: str,
        author_user: discord.Member | discord.User,
    ) -> Optional[discord.Message]:
        # dispatch custom reminder
        embed = discord.Embed(
            title=f"⏰ Reminder for {target_user.display_name}",
            description=f"**Task / Note**:\n{message_text}",
            color=0xE67E22,
        )
        embed.add_field(name="👤 Assigned To", value=target_user.mention, inline=True)
        embed.add_field(name="✍️ Set By", value=author_user.mention, inline=True)
        embed.add_field(name="⏳ Status", value="Pending", inline=True)
        embed.set_footer(text="React with ❤️ to stop reminder pings")

        try:
            msg = await channel.send(
                content=f"🔔 {target_user.mention} — You have a reminder from {author_user.mention}!",
                embed=embed,
            )
            # pre-add heart reaction
            try:
                await msg.add_reaction("❤️")
            except Exception as e:
                logger.warning("Could not add reaction: %s", e)

            reminder_id = f"remind_{msg.id}"
            guild_id = channel.guild.id if hasattr(channel, "guild") and channel.guild else None
            self.db.record_custom_reminder(
                reminder_id=reminder_id,
                message_text=message_text,
                discord_guild_id=guild_id,
                discord_channel_id=channel.id,
                discord_message_id=msg.id,
                target_user_id=str(target_user.id),
                created_by_user_id=str(author_user.id),
            )
            return msg
        except Exception as e:
            logger.error("Failed to dispatch custom reminder: %s", e)
            return None

    async def send_custom_escalation_ping(
        self,
        channel_id: int,
        message_id: int,
        stage: int,
        target_user_id: str,
        message_text: str,
    ) -> bool:
        # custom reminder escalation ping
        channel = await self._get_destination_channel(channel_id)
        if not channel:
            return False

        mention_str = f"<@{target_user_id}>"
        content = f"⏰ {mention_str} Reminder #{stage + 1}: {message_text}"

        rem = self.db.get_custom_reminder_by_message_id(message_id)
        rem_id = rem["reminder_id"] if rem else f"remind_{message_id}"
        prev_pings = self.db.get_custom_reminder_pings(rem_id) if rem_id else []

        try:
            ping_msg = None
            try:
                orig_msg = await channel.fetch_message(message_id)
                ping_msg = await orig_msg.reply(content=content)
            except Exception:
                ping_msg = await channel.send(content=content)

            if ping_msg:
                # delete previous pings
                for prev in prev_pings:
                    prev_id = prev.get("ping_message_id")
                    if prev_id and prev_id != ping_msg.id:
                        try:
                            old_msg = await channel.fetch_message(prev_id)
                            await old_msg.delete()
                        except Exception:
                            pass
                        self.db.delete_custom_reminder_ping(prev_id)

                # pre-add heart reaction
                try:
                    await ping_msg.add_reaction("❤️")
                except Exception as e:
                    logger.warning("Could not add reaction: %s", e)

                self.db.record_custom_reminder_ping(
                    ping_message_id=ping_msg.id,
                    reminder_id=rem_id,
                    discord_message_id=message_id,
                )
            return True
        except Exception as e:
            logger.error("Failed to send reminder escalation ping: %s", e)
            return False

    async def update_acknowledged_reminder_message(
        self,
        channel_id: int,
        message_id: int,
        acknowledged_by_user: discord.User | discord.Member,
        message_text: str,
        reacted_message_id: Optional[int] = None,
    ):
        # update custom reminder
        try:
            channel = await self._get_destination_channel(channel_id)
            if not channel:
                return
            orig_msg = await channel.fetch_message(message_id)
            if orig_msg and orig_msg.embeds:
                old_embed = orig_msg.embeds[0]
                new_embed = discord.Embed(
                    title=f"✅ Reminder Acknowledged: {old_embed.title.replace('⏰ Reminder for ', '')}",
                    description=f"**Task / Note**:\n{message_text}",
                    color=0x2ECC71,
                )
                for field in old_embed.fields:
                    if field.name == "⏳ Status":
                        new_embed.add_field(
                            name="✅ Status",
                            value=f"Acknowledged by {acknowledged_by_user.mention}",
                            inline=True,
                        )
                    else:
                        new_embed.add_field(name=field.name, value=field.value, inline=field.inline)
                new_embed.set_footer(text="Task completed • Reminders stopped")
                await orig_msg.edit(embed=new_embed)

            # reply confirmation
            target_msg = None
            if reacted_message_id and reacted_message_id != message_id:
                try:
                    target_msg = await channel.fetch_message(reacted_message_id)
                except Exception:
                    pass
            target_msg = target_msg or orig_msg
            if target_msg:
                try:
                    await target_msg.reply(
                        f"✅ Task stopped by {acknowledged_by_user.mention}! Reminder pings cleared."
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.warning("Could not update acknowledged reminder message: %s", e)

    async def purge_channel_messages(
        self,
        channel: Any,
        limit: int = 100,
    ) -> tuple[int, int]:
        # purge old bot messages keeping active
        if not hasattr(channel, "purge"):
            return 0, 0

        active_ids = self.db.get_active_message_ids()

        def check(m: discord.Message) -> bool:
            return self.user is not None and m.author.id == self.user.id and m.id not in active_ids

        deleted = []
        try:
            deleted = await channel.purge(limit=limit, check=check, bulk=True)
        except discord.Forbidden:
            deleted = await channel.purge(limit=limit, check=check, bulk=False)
        except Exception as e:
            logger.error("Failed to purge channel messages: %s", e)

        return len(deleted), len(active_ids)

    async def nuke_channel_messages(
        self,
        channel: Any,
        limit: int = 200,
    ) -> int:
        # nuke bot messages and resync
        def check(m: discord.Message) -> bool:
            return self.user is not None and m.author.id == self.user.id

        deleted = []
        if hasattr(channel, "purge"):
            try:
                deleted = await channel.purge(limit=limit, check=check, bulk=True)
            except discord.Forbidden:
                deleted = await channel.purge(limit=limit, check=check, bulk=False)
            except Exception as e:
                logger.error("Failed to nuke channel messages: %s", e)

        # clear tracking and resync
        self.db.clear_all_notion_items()
        if self.scheduler:
            await self.scheduler.poll_notion_once()

        return len(deleted)
