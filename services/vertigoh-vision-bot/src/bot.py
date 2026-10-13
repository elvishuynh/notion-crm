import logging
import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional, Dict, Any
from db import ReviewDatabase

logger = logging.getLogger(__name__)

HEART_EMOJIS = {"❤️", "♥️", "💖", "💗", "💓", "💕"}


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
        intents.reactions = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents, *args, **kwargs)
        self.db = db
        self.reviewer_id = reviewer_id
        self.guild_id = guild_id
        self._register_commands()

    def _register_commands(self):
        """Registers application slash commands and fallback prefix commands."""

        @self.tree.command(
            name="remind",
            description="Set an escalating reminder for a team member with heart reaction acknowledgment",
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
                    f"✅ Escalating reminder scheduled for {target.mention}!",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    "❌ Failed to dispatch reminder. Check bot permissions in this channel.",
                    ephemeral=True,
                )

        @self.command(name="remind", help="Set an escalating reminder: !remind @user <message>")
        async def remind_prefix(ctx: commands.Context, target: discord.Member, *, message: str):
            await self.create_custom_reminder(
                channel=ctx.channel,
                target_user=target,
                message_text=message,
                author_user=ctx.author,
            )

    async def setup_hook(self):
        """Initializes slash commands by syncing to target guild for instant availability."""
        if self.guild_id:
            guild_obj = discord.Object(id=self.guild_id)
            self.tree.copy_global_to(guild=guild_obj)
            await self.tree.sync(guild=guild_obj)
            logger.info("Synchronized application slash commands to guild %s", self.guild_id)
        else:
            await self.tree.sync()
            logger.info("Synchronized global application slash commands")

    async def sync_all_guild_members(self):
        """Scans all connected Discord guilds and synchronizes user profiles into SQLite."""
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
        logger.info("Logged in as Discord Bot: %s (ID: %s)", self.user.name, self.user.id)
        await self.sync_all_guild_members()

    def resolve_reviewer_id(self, reviewer_ref: Optional[str] = None) -> str:
        """Resolves a user ID from either a numeric ID string or a username/handle."""
        ref = reviewer_ref or self.reviewer_id
        if not ref:
            return ""
        if ref.isdigit():
            return ref
        user_match = self.db.find_user_by_name(ref)
        if user_match:
            return user_match["user_id"]
        return ref

    # asset review alerts

    async def send_asset_alert(
        self,
        channel_id: int,
        page_title: str,
        asset_url: str,
        page_url: str,
        reviewer_id: Optional[str] = None,
    ) -> Optional[discord.Message]:
        channel = self.get_channel(channel_id)
        if not channel:
            try:
                channel = await self.fetch_channel(channel_id)
            except Exception as e:
                logger.error("Failed to fetch channel %s: %s", channel_id, e)
                return None

        target_reviewer = self.resolve_reviewer_id(reviewer_id)
        mention_str = f"<@{target_reviewer}>" if target_reviewer else "Reviewer"

        embed = discord.Embed(
            title=f"🎬 New Asset Ready for Review: {page_title}",
            description="A new asset has been attached to the Kai Content Schedule.",
            color=0xF1C40F,  # alert color
        )
        embed.add_field(name="🔗 Asset Link", value=f"[Open Asset Video/Media]({asset_url})", inline=False)
        embed.add_field(name="📋 Notion Board", value=f"[View Notion Page]({page_url})", inline=False)
        embed.add_field(name="👤 Assigned Reviewer", value=mention_str, inline=True)
        embed.add_field(name="⏳ Status", value="Pending Review", inline=True)
        embed.set_footer(text="React with ❤️ to approve and dismiss reminder pings.")

        try:
            msg = await channel.send(
                content=f"🔔 {mention_str} — A new asset is ready for your review!",
                embed=embed,
            )
            await msg.add_reaction("❤️")
            return msg
        except Exception as e:
            logger.error("Failed to send asset alert to channel %s: %s", channel_id, e)
            return None

    async def send_escalation_ping(
        self,
        channel_id: int,
        message_id: int,
        stage: int,
        reviewer_id: str,
        page_title: str,
        asset_url: str,
    ) -> bool:
        """Sends an escalating reminder ping referencing the pending review."""
        channel = self.get_channel(channel_id)
        if not channel:
            try:
                channel = await self.fetch_channel(channel_id)
            except Exception as e:
                logger.error("Failed to fetch channel %s for escalation: %s", channel_id, e)
                return False

        target_reviewer = self.resolve_reviewer_id(reviewer_id)
        mention_str = f"<@{target_reviewer}>" if target_reviewer else "Reviewer"

        if stage == 0:
            header = "⚠️ **[12-Hour Reminder]**"
            urgency = "This asset has been pending review for **12 hours**."
        elif stage == 1:
            header = "🚨 **[20-Hour Reminder]**"
            urgency = "This asset has been pending review for **20 hours** (+8h since last reminder)."
        elif stage == 2:
            header = "🔥 **[24-Hour Urgent Reminder]**"
            urgency = "This asset has been pending review for **24 hours**. Reminders will now repeat **every 2 hours** until reviewed."
        else:
            header = f"⏰ **[Overdue Escalation - Cycle {stage - 2}]**"
            urgency = "Action required: Asset review is critically overdue (repeating every 2h)."

        content = (
            f"{header} {mention_str}\n"
            f"{urgency}\n"
            f"**Content**: *{page_title}*\n"
            f"**Link**: {asset_url}\n"
            f"👉 Please react with ❤️ on the original message to acknowledge."
        )

        try:
            try:
                orig_msg = await channel.fetch_message(message_id)
                await orig_msg.reply(content=content)
            except Exception:
                await channel.send(content=content)
            return True
        except Exception as e:
            logger.error("Failed to send escalation ping: %s", e)
            return False

    async def update_reviewed_message(
        self,
        channel_id: int,
        message_id: int,
        reviewer_user: discord.User | discord.Member,
        page_title: str,
    ):
        """Updates the original message embed to green [Reviewed ✅] state."""
        try:
            channel = self.get_channel(channel_id) or await self.fetch_channel(channel_id)
            orig_msg = await channel.fetch_message(message_id)
            if orig_msg and orig_msg.embeds:
                old_embed = orig_msg.embeds[0]
                new_embed = discord.Embed(
                    title=f"✅ Asset Reviewed: {page_title}",
                    description="This asset has been reviewed and acknowledged.",
                    color=0x2ECC71,  # success color
                )
                for field in old_embed.fields:
                    if field.name == "⏳ Status":
                        new_embed.add_field(name="✅ Status", value=f"Reviewed by {reviewer_user.mention}", inline=True)
                    else:
                        new_embed.add_field(name=field.name, value=field.value, inline=field.inline)
                new_embed.set_footer(text="Review completed. All future reminders stopped.")
                await orig_msg.edit(embed=new_embed)
                await orig_msg.reply(
                    f"🎉 Review recorded by {reviewer_user.mention}! Reminders have been stopped."
                )
        except Exception as e:
            logger.warning("Could not update original Discord message %s: %s", message_id, e)

    # custom reminder commands

    async def create_custom_reminder(
        self,
        channel: discord.abc.Messageable,
        target_user: discord.Member | discord.User,
        message_text: str,
        author_user: discord.Member | discord.User,
    ) -> Optional[discord.Message]:
        embed = discord.Embed(
            title=f"⏰ Reminder for {target_user.display_name}",
            description=f"**Task / Note**:\n{message_text}",
            color=0xE67E22,  # reminder color
        )
        embed.add_field(name="👤 Assigned To", value=target_user.mention, inline=True)
        embed.add_field(name="✍️ Set By", value=author_user.mention, inline=True)
        embed.add_field(name="⏳ Status", value="Pending Acknowledgment", inline=True)
        embed.set_footer(text="React with ❤️ to acknowledge and dismiss reminder pings.")

        try:
            msg = await channel.send(
                content=f"🔔 {target_user.mention} — You have a reminder from {author_user.mention}!",
                embed=embed,
            )
            await msg.add_reaction("❤️")
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
            logger.info("Recorded custom reminder %s for %s", reminder_id, target_user.id)
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
        """Sends an escalating ping for a custom reminder on the 12h -> 8h -> 4h -> 2h schedule."""
        channel = self.get_channel(channel_id)
        if not channel:
            try:
                channel = await self.fetch_channel(channel_id)
            except Exception as e:
                logger.error("Failed to fetch channel %s for reminder escalation: %s", channel_id, e)
                return False

        mention_str = f"<@{target_user_id}>"

        if stage == 0:
            header = "⚠️ **[12-Hour Reminder]**"
            urgency = "This task has been pending for **12 hours**."
        elif stage == 1:
            header = "🚨 **[20-Hour Reminder]**"
            urgency = "This task has been pending for **20 hours** (+8h since last reminder)."
        elif stage == 2:
            header = "🔥 **[24-Hour Urgent Reminder]**"
            urgency = "This task is **24 hours overdue**. Reminders will now repeat **every 2 hours** until acknowledged."
        else:
            header = f"⏰ **[Overdue Escalation - Cycle {stage - 2}]**"
            urgency = "Action required: Reminder is critically overdue (repeating every 2h)."

        content = (
            f"{header} {mention_str}\n"
            f"{urgency}\n"
            f"**Task**: *{message_text}*\n"
            f"👉 Please react with ❤️ on the original message to acknowledge."
        )

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
        """Updates a custom reminder embed to green [Acknowledged ✅] state."""
        try:
            channel = self.get_channel(channel_id) or await self.fetch_channel(channel_id)
            orig_msg = await channel.fetch_message(message_id)
            if orig_msg and orig_msg.embeds:
                old_embed = orig_msg.embeds[0]
                new_embed = discord.Embed(
                    title=f"✅ Reminder Acknowledged: {old_embed.title.replace('⏰ Reminder for ', '')}",
                    description=f"**Task / Note**:\n{message_text}",
                    color=0x2ECC71,  # resolved color
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
                new_embed.set_footer(text="Task acknowledged. All reminder pings stopped.")
                await orig_msg.edit(embed=new_embed)
                await orig_msg.reply(
                    f"🎉 Task acknowledged by {acknowledged_by_user.mention}! All future pings stopped."
                )
        except Exception as e:
            logger.warning("Could not update acknowledged reminder message %s: %s", message_id, e)

    # reaction listener

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if self.user and payload.user_id == self.user.id:
            return

        emoji_name = payload.emoji.name
        if emoji_name not in HEART_EMOJIS:
            return

        # check asset reviews
        asset = self.db.get_asset_by_message_id(payload.message_id)
        if asset and asset["status"] == "PENDING":
            updated = self.db.mark_reviewed_by_message_id(
                payload.message_id,
                reviewer_id=str(payload.user_id),
            )
            if updated:
                logger.info("Asset %s marked REVIEWED by %s", asset["notion_page_id"], payload.user_id)
                user = self.get_user(payload.user_id) or await self.fetch_user(payload.user_id)
                await self.update_reviewed_message(
                    channel_id=payload.channel_id,
                    message_id=payload.message_id,
                    reviewer_user=user,
                    page_title=asset["page_title"],
                )
            return

        # check manual reminders
        custom_rem = self.db.get_custom_reminder_by_message_id(payload.message_id)
        if custom_rem and custom_rem["status"] == "PENDING":
            updated = self.db.mark_custom_reminder_acknowledged(
                discord_message_id=payload.message_id,
                acknowledged_by=str(payload.user_id),
            )
            if updated:
                logger.info("Custom reminder %s marked ACKNOWLEDGED by %s", custom_rem["reminder_id"], payload.user_id)
                user = self.get_user(payload.user_id) or await self.fetch_user(payload.user_id)
                await self.update_acknowledged_reminder_message(
                    channel_id=payload.channel_id,
                    message_id=payload.message_id,
                    acknowledged_by_user=user,
                    message_text=custom_rem["message_text"],
                )
            return
