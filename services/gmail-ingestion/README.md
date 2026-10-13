# Service: Gmail Ingestion Pipeline

An automated CRM pipeline connecting inbound brand emails in Gmail to a Notion database, powered by Google Gemini AI and Google Apps Script.

## Features
- **Automated Ingestion**: Runs on a 15-minute time trigger to capture inbound starred business emails.
- **AI Extraction**: Uses `gemini-3.5-flash-lite` with exponential retry backoff to extract company name, 1-sentence description, and opportunity type.
- **Reply Status Tracking**: Automatically checks whether the last sender was you or the brand to tag `Needs Reply` vs `Waiting on Reply`.
- **True Idempotency**: Queries Notion by `Thread ID` before extraction and creation, preventing duplicate cards even on backfill.
- **Thread Context Storage**: Embeds original subject and email body snippet as structured callout blocks inside each Notion card.
- **Rate-Limit Safe**: Built-in 350ms pacing and exponential retry handling for Notion's 3 req/sec rate limit.

## Files
- `Code.gs`: Core Apps Script logic.
- `appsscript.json`: Google Apps Script V8 runtime manifest.
- `.claspignore`: File ignore list for Clasp deployments.

## Deployment
This service is deployed automatically via GitHub Actions whenever changes are made in `services/gmail-ingestion/`. You can also manually push using:
```bash
clasp push
```
