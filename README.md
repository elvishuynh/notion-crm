# Notion CRM

Monorepo containing automation services and CRM tools connected to Notion.

## Services

```
notion-crm/
├── services/
│   ├── gmail-ingestion/      # Gmail -> Notion CRM Pipeline (Google Apps Script)
│   └── vertigoh-vision-bot/  # Notion Asset Discord Reviewer Bot (Python / Docker)
├── scripts/                  # Setup and diagnostic utilities
└── .github/workflows/        # CI/CD and automated test workflows
```

---

## 1. Gmail Ingestion Pipeline (`services/gmail-ingestion`)

Captures inbound starred brand emails in Gmail, extracts deal metadata with Google Gemini, and creates structured cards in Notion.

### Features
- **Scheduled Ingestion**: Polls starred threads on a 15-minute trigger.
- **AI Extraction**: Uses `gemini-3.5-flash-lite` to extract brand name, description, and opportunity type.
- **Reply Status**: Detects whether the last message was inbound or outbound (`Needs Reply` vs `Waiting on Reply`).
- **Idempotency**: Verifies existing `Thread ID` in Notion before inserting records.
- **Context Blocks**: Archives the email snippet directly in the Notion page body.

### Setup
1. Create a Google Apps Script project at [script.google.com](https://script.google.com/).
2. Push `services/gmail-ingestion/Code.gs` and `appsscript.json` (or use `clasp push`).
3. Set Script Properties in **Project Settings**:
   - `GEMINI_API_KEY`: Google Gemini API key.
   - `NOTION_API_KEY`: Notion internal integration secret.
   - `NOTION_DATABASE_ID`: Target Notion database ID.
4. Add a time-driven trigger for `syncOpportunityEmails` (every 15 minutes).

---

## 2. Vertigoh Vision Bot (`services/vertigoh-vision-bot`)

Monitors Notion content schedule boards for new video/media assets and posts review alerts to Discord with escalating reminders until acknowledged.

### Features
- **Asset Alerts**: Posts rich embeds with pre-reacted ❤️ for immediate review.
- **Escalation Timeline**:
  - $T_0$: Initial alert upon asset detection.
  - +12h: First reminder ping.
  - +8h: Second reminder ping.
  - +4h: Third reminder ping.
  - +2h recurring: Continued pings until reviewed.
- **Reaction Resolution**: Listens for heart reactions (`❤️`, `💖`, etc.) to mark records reviewed and halt pings.
- **Custom Reminders**: `/remind` slash command with the same tiered escalation schedule.
- **Persistent State**: SQLite tracking store surviving restarts.

### Setup
```bash
cd services/vertigoh-vision-bot
cp .env.example .env
# configure tokens in .env
pip install -r requirements.txt
python src/main.py
```

Or deploy with Docker:
```bash
docker compose up -d --build
```

---

## 3. Workflows & CI/CD
- **`test-vertigoh-vision-bot.yml`**: Runs pytest unit test suite on pull requests and pushes to `main`.
- **`docker-publish.yml`**: Builds and publishes the bot image to GitHub Container Registry (`ghcr.io`).
- **`deploy.yml`**: Deploys `services/gmail-ingestion` to Google Apps Script via Clasp.
