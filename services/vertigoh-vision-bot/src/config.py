import os
from pathlib import Path
from dotenv import load_dotenv

# load env from file
BASE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BASE_DIR.parent.parent

for candidate in [BASE_DIR / ".env", REPO_ROOT / ".env"]:
    if candidate.exists():
        load_dotenv(candidate)
        break

NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")
NOTION_PAGE_ID = os.getenv("NOTION_PAGE_ID", "")
NOTION_VERSION = os.getenv("NOTION_VERSION", "2022-06-28")

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "0")) if os.getenv("DISCORD_GUILD_ID", "0").isdigit() else None
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0")) if os.getenv("DISCORD_CHANNEL_ID", "0").isdigit() else 0
REVIEWER_DISCORD_ID = os.getenv("REVIEWER_DISCORD_ID", "")

DEFAULT_DB_PATH = str(BASE_DIR / "data" / "review_state.db")
DATABASE_PATH = os.getenv("DATABASE_PATH", DEFAULT_DB_PATH)

NOTION_POLL_INTERVAL = int(os.getenv("NOTION_POLL_INTERVAL", "60"))
SCHEDULER_INTERVAL = int(os.getenv("SCHEDULER_INTERVAL", "30"))


def validate_config(require_discord: bool = True):
    errors = []
    if not NOTION_API_KEY:
        errors.append("NOTION_API_KEY is not configured.")
    if not NOTION_PAGE_ID:
        errors.append("NOTION_PAGE_ID is not configured.")
    if require_discord:
        if not DISCORD_BOT_TOKEN:
            errors.append("DISCORD_BOT_TOKEN is not configured.")
        if not DISCORD_CHANNEL_ID:
            errors.append("DISCORD_CHANNEL_ID must be a valid Discord channel integer ID.")
        if not REVIEWER_DISCORD_ID:
            errors.append("REVIEWER_DISCORD_ID is not configured.")
    if errors:
        raise ValueError("Configuration validation failed:\n - " + "\n - ".join(errors))
