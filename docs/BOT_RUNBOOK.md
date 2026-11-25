# Bot Runbook

All commands are run from the repo root (`/mnt/c/Users/kavin/OneDrive/Desktop/Break-glass/Ananth-Project-`).

## 1. Configure once

```bash
cp .env.example .env
```

Set these in `.env` (never commit real values):

- `TELEGRAM_BOT_TOKEN` — required; bot exits without it.
- `BOT_CALLBACK_SECRET` — set to a stable random value so approval/enrollment buttons survive bot restarts.
- `BOT_INTERNAL_TOKEN` — required for the live-state bridge; the backend sends it as `Authorization: Bearer ...`.
- `BOT_INTERNAL_PORT` — default `8765`; the bridge binds only to `127.0.0.1` (or `::1`).
- `AI_MODE` — `fixture` for local demo, `live` for real Detectif calls.
- `DETECTIF_API_KEY` — required when `AI_MODE=live`.
- `DATABASE_PATH` and `UPLOAD_DIR` — leave unset to use `backend/data/`, shared by the API and the bot.
- `TELEGRAM_USER_LINKS` — restrict who may use admin/approver commands until bot identity binding is tightened.

System dependency: `ffmpeg` must be on `PATH`; voice notes are converted to 16 kHz mono WAV with `shell=False`.

## 2. Start backend, bot, and bridge

There is no separate bridge process: `BotInternalBridge` starts inside the bot process during `post_init` when `BOT_INTERNAL_TOKEN` is set, and the backend posts live-state updates to `http://127.0.0.1:$BOT_INTERNAL_PORT/internal/live-state`.

Terminal 1 — backend API:

```bash
.venv/bin/python -m uvicorn backend.app.main:app --port 8000
```

Terminal 2 — Telegram bot (long polling; also starts the loopback bridge):

```bash
.venv/bin/python -m backend.app.bot.bot
```

Verify health from a third terminal:

```bash
curl http://127.0.0.1:8000/health
```

If `BOT_INTERNAL_TOKEN` is unset, the bot still runs but logs that the bridge is disabled; live chunks still write the audit chain, but Telegram live alerts are not pushed.

## 3. Behaviour to expect

- A check (voice note/reply) is rate-limited to 5 per 10 minutes per Telegram user; enrollment uploads are a separate flow with a 5-sample lifetime cap.
- Enrollment requires an explicit consent button before any audio is processed; raw files are removed from disk and the bot attempts to delete the Telegram message.
- Every voice check, approval decision, block, expiry, and enrollment step appends to the same hash-chained `audit_events` table. If audit logging fails, the bot fails closed and aborts the action.
- `/report` in an approver chat returns the verified chain status; `/approver` and `/requester` currently trust a self-supplied employee ID, so restrict access to trusted chats/users.
- The internal bridge rejects any non-loopback bind host, compares the Bearer token in constant time, and caps request bodies at 64 KB.

## 4. Verification commands

```bash
.venv/bin/python -m pytest backend/app -q
npm run lint
npm run build
```
