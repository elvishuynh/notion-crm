import asyncio
import logging
import signal
import sys
from pathlib import Path

from config import (
    NOTION_API_KEY,
    NOTION_PAGE_ID,
    NOTION_VERSION,
    DISCORD_BOT_TOKEN,
    DISCORD_GUILD_ID,
    DISCORD_CHANNEL_ID,
    REVIEWER_DISCORD_ID,
    DATABASE_PATH,
    NOTION_POLL_INTERVAL,
    SCHEDULER_INTERVAL,
    validate_config,
)
from db import ReviewDatabase
from notion_client import NotionClient
from bot import VertigohVisionBot
from scheduler import ReviewScheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("vertigoh_vision_bot")


async def main():
    logger.info("Initializing Vertigoh Vision Bot Service...")
    validate_config(require_discord=True)

    db = ReviewDatabase(DATABASE_PATH)
    notion = NotionClient(
        api_key=NOTION_API_KEY,
        page_or_db_id=NOTION_PAGE_ID,
        notion_version=NOTION_VERSION,
    )
    bot = VertigohVisionBot(
        db=db,
        reviewer_id=REVIEWER_DISCORD_ID,
        guild_id=DISCORD_GUILD_ID,
    )

    scheduler = ReviewScheduler(
        bot=bot,
        notion=notion,
        db=db,
        channel_id=DISCORD_CHANNEL_ID,
        reviewer_id=REVIEWER_DISCORD_ID,
        notion_interval=NOTION_POLL_INTERVAL,
        escalation_interval=SCHEDULER_INTERVAL,
    )

    scheduler_task = None

    @bot.event
    async def on_ready():
        nonlocal scheduler_task
        logger.info("Discord Bot connected as %s (ID: %s)", bot.user.name, bot.user.id)
        if not scheduler_task:
            logger.info("Launching background ReviewScheduler...")
            scheduler_task = asyncio.create_task(scheduler.start())

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown(bot, scheduler, scheduler_task)))
        except NotImplementedError:
            pass  # windows compatibility

    try:
        await bot.start(DISCORD_BOT_TOKEN)
    except Exception as e:
        logger.error("Bot encountered fatal error: %s", e)
    finally:
        await shutdown(bot, scheduler, scheduler_task)


async def shutdown(bot, scheduler, scheduler_task):
    logger.info("Initiating graceful shutdown...")
    if scheduler:
        scheduler.stop()
    if scheduler_task and not scheduler_task.done():
        scheduler_task.cancel()
    if bot and not bot.is_closed():
        await bot.close()
    logger.info("Shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
