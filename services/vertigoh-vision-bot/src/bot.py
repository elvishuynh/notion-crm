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

HEART_EMOJIS = set()


class VertigohVisionBot(commands.Bot):
    def __init__(
        self,
        db: ReviewDatabase,
        reviewer_id: str,
        guild_id: Optional[int] = None,
        *args,
        **kwargs,
    ):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents, *args, **kwargs)
        self.db = db
        self.reviewer_id = reviewer_id
        self.guild_id = guild_id
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
        embed = discord.Embed(
            title=f"🎬 Review Pending: {page_title}",
            description="Asset ready for review on Notion schedule board.",
            color=0xF1C40F,
        )
        embed.add_field(name="🔗 Asset Link", value=f"[Open Asset Video/Media]({asset_url})", inline=False)
        embed.add_field(name="📋 Notion Board", value=f"[View Notion Page]({page_url})", inline=False)
        embed.add_field(name="👤 Assigned Role(s)", value=mentions_str, inline=True)
        embed.add_field(name="⏳ Status", value="Review Pending", inline=True)
        embed.set_footer(text="Tracked via Notion • Escalating reminders (+12h, +8h, +4h, +2h)")

        try:
            msg = await channel.send(
                content=f"🔔 {mentions_str} — **{page_title}** is ready for review!",
                embed=embed,
                allowed_mentions=discord.AllowedMentions(roles=True, users=True),
            )
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
        embed = discord.Embed(
            title=f"✂️ Edit Pending: {page_title}",
            description="Asset ready for editing on Notion schedule board.",
            color=0x3498DB,
        )
        embed.add_field(name="🔗 Asset Link", value=f"[Open Asset Video/Media]({asset_url})", inline=False)
        embed.add_field(name="📋 Notion Board", value=f"[View Notion Page]({page_url})", inline=False)
        embed.add_field(name="✏️ Assigned Role(s)", value=mentions_str, inline=True)
        embed.add_field(name="⏳ Status", value="Edit Pending", inline=True)
        embed.set_footer(text="Tracked via Notion • Daily reminder (every 24h)")

        try:
            msg = await channel.send(
                content=f"🔔 {mentions_str} — **{page_title}** is ready for edits!",
                embed=embed,
                allowed_mentions=discord.AllowedMentions(roles=True, users=True),
            )
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
        embed = discord.Embed(
            title=f"🚀 Ready to Post: {page_title}",
            description="Asset is approved and ready to post on Notion schedule board.",
            color=0x9B59B6,
        )
        embed.add_field(name="🔗 Asset Link", value=f"[Open Asset Video/Media]({asset_url})", inline=False)
        embed.add_field(name="📋 Notion Board", value=f"[View Notion Page]({page_url})", inline=False)
        embed.add_field(name="📤 Assigned Role(s)", value=mentions_str, inline=True)
        embed.add_field(name="⏳ Status", value="Ready to Post", inline=True)
        embed.set_footer(text="Tracked via Notion • Daily reminder (every 24h) until Scheduled")

        try:
            msg = await channel.send(
                content=f"🔔 {mentions_str} — **{page_title}** is ready to post!",
                embed=embed,
                allowed_mentions=discord.AllowedMentions(roles=True, users=True),
            )
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
    ) -> bool:
        # send reminder ping
        channel = await self._get_destination_channel(channel_id)
        if not channel:
            return False

        guild = channel.guild if hasattr(channel, "guild") else None
        mentions_str = self.resolve_role_mentions(assigned_users, guild)
        cat_upper = category.upper()

        if cat_upper == "EDIT":
            content = (
                f"⏰ **[24h Edit Reminder #{stage + 1}]** {mentions_str} — **{page_title}** is still pending edits!\n"
                f"🔗 **Asset**: {asset_url}\n"
                f"📋 **Notion**: {page_url}"
            )
        elif cat_upper in ("POST", "READY_TO_POST"):
            content = (
                f"🚀 **[24h Ready to Post Reminder #{stage + 1}]** {mentions_str} — **{page_title}** is ready to post!\n"
                f"🔗 **Asset**: {asset_url}\n"
                f"📋 **Notion**: {page_url}"
            )
        else:
            # review escalation header
            if stage == 0:
                header = "⚠️ **[12-Hour Review Reminder]**"
            elif stage == 1:
                header = "🚨 **[20-Hour Review Reminder]**"
            elif stage == 2:
                header = "🔥 **[24-Hour Urgent Review Reminder]**"
            else:
                header = f"⏰ **[Review Overdue - Cycle {stage - 2}]**"

            content = (
                f"{header} {mentions_str} — **{page_title}** is still pending review!\n"
                f"🔗 **Asset**: {asset_url}\n"
                f"📋 **Notion**: {page_url}"
            )

        try:
            try:
                orig_msg = await channel.fetch_message(message_id)
                await orig_msg.reply(content=content, allowed_mentions=discord.AllowedMentions(roles=True, users=True))
            except Exception:
                await channel.send(content=content, allowed_mentions=discord.AllowedMentions(roles=True, users=True))
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
                cat_norm = category.upper()
                if cat_norm == "REVIEW":
                    prefix = "🎬"
                elif cat_norm == "EDIT":
                    prefix = "✂️"
                else:
                    prefix = "🚀"
                new_embed = discord.Embed(
                    title=f"{prefix} [Resolved] {page_title}",
                    description=old_embed.description,
                    color=0x2ECC71,
                )
                for field in old_embed.fields:
                    if field.name == "⏳ Status":
                        new_embed.add_field(
                            name="✅ Status",
                            value=f"Resolved in Notion ({current_status})",
                            inline=True,
                        )
                    else:
                        new_embed.add_field(name=field.name, value=field.value, inline=field.inline)
                new_embed.set_footer(text="Resolved via Notion update • Reminders stopped")
                await orig_msg.edit(embed=new_embed)
                await orig_msg.reply(
                    f"✅ **{page_title}** is resolved (Notion status: **{current_status}**). Reminders stopped."
                )
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

        try:
            msg = await channel.send(
                content=f"🔔 {target_user.mention} — You have a reminder from {author_user.mention}!",
                embed=embed,
            )
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
        content = f"⏰ **[Reminder Ping]** {mention_str} — Task: *{message_text}*"

        try:
            try:
                orig_msg = await channel.fetch_message(message_id)
                await orig_msg.reply(content=content)
            except Exception:
                await channel.send(content=content)
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
                await orig_msg.edit(embed=new_embed)
        except Exception as e:
            logger.warning("Could not update acknowledged reminder message: %s", e)
