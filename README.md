# Notion CRM

Monorepo for Notion automation services and CRM tools

## Services

```
notion-crm/
├── services/
│   ├── gmail-ingestion/      # Gmail to Notion CRM pipeline (Google Apps Script)
│   └── vertigoh-vision-bot/  # Notion asset alerts and reminders (Python / Discord)
├── scripts/                  # Setup and diagnostic scripts
└── .github/workflows/        # CI/CD workflows
```

---

## 1. Gmail Ingestion (`services/gmail-ingestion`)

Pulls starred brand emails from Gmail, extracts deal info with Gemini, and adds cards to Notion

### Features
- Scheduled sync: polls starred threads every 15 minutes
- AI extraction: uses `gemini-3.5-flash-lite` to pull company name, short description, and opportunity type
- Reply tracking: sets `Needs Reply` or `Waiting on Reply` based on the latest sender
- Duplicate prevention: checks `Thread ID` in Notion before creating pages
- Email body callouts: inserts thread subject and snippet into the Notion page

### Setup
1. Create a Google Apps Script project at [script.google.com](https://script.google.com/)
2. Push `Code.gs` and `appsscript.json` manually or with `clasp push`
3. Add Script Properties in Project Settings:
   - `GEMINI_API_KEY`: Google Gemini API key
   - `NOTION_API_KEY`: Notion integration secret
   - `NOTION_DATABASE_ID`: Notion database ID
4. Add a time trigger for `syncOpportunityEmails` running every 15 minutes

Utility functions in `Code.gs`:
- `testGeminiConnection`: verifies Gemini API key
- `testNotionConnection`: verifies Notion database access
- `resetAndBackfillAllOpportunities`: backfills all starred emails

---

## 2. Vertigoh Vision Bot (`services/vertigoh-vision-bot`)

Monitors Notion content schedule boards for assets, pings team members in Discord, and runs escalating reminders

### Pipeline Stages
- Review Pending: alerts reviewers, pings every 12h until reviewed
- Edit Pending: alerts editors, pings every 12h until edited
- Ready to Post: alerts posters, pings every 12h until scheduled or posted

### Reaction and Status Sync
Alert embeds and reminder pings include action emojis:
- 👀 Review Pending
- 🎬 Edit Pending
- ↗️ Ready to Post
- ✅ Posted
- ❤️ Acknowledge and resolve

Reacting with an emoji or replying with a keyword or emoji (`posted`, `scheduled`, `done`, `stop`, `acknowledged`, `❤️`) updates the page status in Notion directly and stops reminder pings.

### Commands
- `/remind <target> <message>`: schedule escalating task reminder for a team member
- `/snooze`: pause reminder pings for 12 hours
- `/unsnooze`: cancel snooze and resume pings
- `/status`: show active reminders and current snooze state

### Setup
```bash
cd services/vertigoh-vision-bot
cp .env.example .env
# fill in keys and ids in .env
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python src/main.py
```

Docker:
```bash
docker compose up -d --build
```

Tests:
```bash
pytest tests/ -v
```

---

## 3. Scripts (`scripts/`)

- `setup_notion_database.py`: creates properties and view order on the Notion CRM database
- `test_discord.py`: tests Discord bot connection and channel permissions

---

## 4. Workflows
- `test-vertigoh-vision-bot.yml`: runs pytest on PRs and pushes to main
- `docker-publish.yml`: builds and pushes Docker image to GHCR
- `deploy.yml`: deploys `gmail-ingestion` to Google Apps Script via Clasp on push to main
