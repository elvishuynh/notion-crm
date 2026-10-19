# Vertigoh Vision Bot

Monitors Notion content schedule boards for new video/media assets and posts review alerts to Discord with escalating reminders until acknowledged.

## Features
- **Immediate Alerting**: Posts a rich embed with the asset link and action reactions.
- **Escalation Schedule**: Alert reminders ping at a maximum interval of every 12 hours until acknowledged or resolved.
- **Reaction and Reply Resolution**: Reacting to alerts or pings (`❤️`, `✅`, status emojis) or replying with keywords (`posted`, `scheduled`, `done`, `stop`, `acknowledged`) updates Notion and clears reminder pings.
- **Custom Reminders**: `/remind` slash command with escalating reminders for ad-hoc tasks.
- **Persistent State**: SQLite store persists asset status, message IDs, and ping schedules.

## Configuration

Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```

Configure the environment variables:
- `NOTION_API_KEY`: Notion internal integration secret (`ntn_...`).
- `NOTION_PAGE_ID`: Notion page or database ID.
- `DISCORD_BOT_TOKEN`: Discord Bot Token.
- `DISCORD_CHANNEL_ID`: Discord Channel ID for alerts.
- `DISCORD_GUILD_ID`: Optional Guild ID for instant slash command sync.
- `REVIEWER_DISCORD_ID`: User ID or username to mention.

## Running

### Local Development
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python src/main.py
```

### Docker
```bash
docker compose up -d --build
```

### Tests
```bash
pytest tests/ -v
```
