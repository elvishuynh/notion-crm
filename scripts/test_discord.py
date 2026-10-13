import asyncio
import logging
import sys
from pathlib import Path

# module path setup
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "vertigoh-vision-bot" / "src"))

from config import (
    DISCORD_BOT_TOKEN,
    DISCORD_CHANNEL_ID,
    REVIEWER_DISCORD_ID,
)
import discord

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("discord_test")


async def run_test():
    if not DISCORD_BOT_TOKEN or not DISCORD_CHANNEL_ID:
        print("\nError: DISCORD_BOT_TOKEN and DISCORD_CHANNEL_ID must be set in services/vertigoh-vision-bot/.env\n")
        sys.exit(1)

    intents = discord.Intents.default()
    intents.message_content = True
    intents.reactions = True
    intents.members = True

    client = discord.Client(intents=intents)
    reaction_received = asyncio.Event()
    test_msg = None

    @client.event
    async def on_ready():
        nonlocal test_msg
        print(f"\nConnected to Discord as: {client.user.name} (ID: {client.user.id})")
        channel = client.get_channel(DISCORD_CHANNEL_ID) or await client.fetch_channel(DISCORD_CHANNEL_ID)
        
        mention_str = f"<@{REVIEWER_DISCORD_ID}>" if REVIEWER_DISCORD_ID else "Reviewer"
        
        embed = discord.Embed(
            title="[Test] Vertigoh Vision Bot",
            description="Testing bot connection and reaction listener. React with ❤️ below.",
            color=0x3498DB,
        )
        embed.add_field(name="Target Reviewer", value=mention_str, inline=True)
        embed.add_field(name="Status", value="Waiting for reaction", inline=True)
        
        test_msg = await channel.send(content=f"Hey {mention_str}, test message from vertigoh vision bot.", embed=embed)
        await test_msg.add_reaction("❤️")
        print(f"Test message sent to channel #{channel.name} (Message ID: {test_msg.id})")

    @client.event
    async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
        if not test_msg or payload.message_id != test_msg.id or payload.user_id == client.user.id:
            return
        if payload.emoji.name in ("❤️", "♥️", "💖", "💗", "💓", "💕"):
            print(f"\nHeart reaction detected from User ID {payload.user_id}")
            new_embed = discord.Embed(
                title="[Test Passed] Reaction Confirmed",
                description="Reaction listener successfully received the heart reaction.",
                color=0x2ECC71,
            )
            mention_str = f"<@{REVIEWER_DISCORD_ID}>" if REVIEWER_DISCORD_ID else "Reviewer"
            new_embed.add_field(name="Target Reviewer", value=mention_str, inline=True)
            new_embed.add_field(name="Status", value=f"Approved by <@{payload.user_id}>", inline=True)
            await test_msg.edit(embed=new_embed)
            await test_msg.reply("Test passed: permissions and reactions working.")
            reaction_received.set()

    # start bot client
    bot_task = asyncio.create_task(client.start(DISCORD_BOT_TOKEN))

    try:
        # wait for reaction event
        await asyncio.wait_for(reaction_received.wait(), timeout=180)
        print("\nTest completed successfully.")
    except asyncio.TimeoutError:
        print("\nTimed out waiting for reaction.")
    finally:
        await client.close()
        bot_task.cancel()


if __name__ == "__main__":
    asyncio.run(run_test())
