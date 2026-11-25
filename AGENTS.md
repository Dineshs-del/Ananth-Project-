# AGENTS.md

## Layout
- `src/` is an AI Studio Vite/React 19 UI; `App.tsx` uses `src/lib/voiceGateEngine.ts`. Default "Demo Engine" reimplements the workflow in-browser; "Live FastAPI" calls `backend/app/main.py` (base `http://127.0.0.1:8000`, override with `VITE_API_BASE_URL`). Mirror workflow changes in both the engine and `backend/app/approval/*`.
- Backend: `approval/` (workflow, decision table, executor), `ai/voice_check.py` + `ai/detectif_client.py` (Aletheia/Eidos via Detectif), `core/` (config/DB/storage), `audit/` (hash chain), `live/` (Live Call Guard sessions).
- `backend/app/bot/` is a separate Telegram long-polling path: Aletheia-only (ignores Eidos), ffmpeg to 16 kHz mono WAV, 4–120 s/20 MB, 5 checks/10 min/user. In fixture mode it reuses `verify_*.json` but reads `aletheia` plus stored `risk` for a BORDERLINE special case.

## Commands
- Frontend: `npm ci`, `npm run dev` (port 3000, host 0.0.0.0), `npm run build`, `npm run lint` (= `tsc --noEmit`; no ESLint or test script).
- Use `.venv/bin/python`; system `python3` lacks backend deps. No full Python manifest: core needs `fastapi`, `uvicorn`, `httpx`, `pytest`, `python-dotenv`; bot needs `requirements-bot.txt` plus system `ffmpeg`.
- API from repo root: `.venv/bin/python -m uvicorn backend.app.main:app --port 8000`.
- Tests from repo root: `.venv/bin/python -m pytest backend/app -q`. Most tests have a `backend.app.*` ↔ `app.*` import fallback, so cwd matters; `live/test_live_guard.py` and `scripts/live_smoke.py` do not. Tests use tmp SQLite/`httpx.MockTransport`, no external services.
- Baseline: full root run stops on 2 collection errors (`bot/test_live_call_alerts.py`, `live/test_live_guard.py`); ignoring those gives 1 failed (stale `/health` assertion in `test_backend_core.py`) and 74 passed. Async alert tests need `pytest-asyncio`, which is not installed.
- Bot: `.venv/bin/python -m backend.app.bot.bot` (long polling; needs `TELEGRAM_BOT_TOKEN`).
- Enrollment CLI: `.venv/bin/python -m backend.app.ai.enrollment <employee_id> <audio_paths...>`; forces live mode, so `DETECTIF_API_KEY` is required even when `AI_MODE=fixture`.
- Live raw Detectif capture: `.venv/bin/python -m backend.app.ai.capture_fixtures <wav_path> --name <label>` → `backend/fixtures/raw_<label>.json`. Live smoke: `.venv/bin/python -m backend.scripts.live_smoke <wav_path> --label <label>` requires `AI_MODE=live`; at most one enroll, exactly one verify with retries disabled; writes `raw_<label>.json` and never prints keys.

## Config and environment
- `.env` loads from repo root, `backend/`, then CWD in that order with `override=False`, so the first definition wins. `.env*` is gitignored except tracked `.env.example`.
- `AI_MODE` defaults to `fixture`; `FIXTURE_SCENARIO` (`pass|borderline|fail|error`) selects `backend/fixtures/verify_<scenario>.json`. Fixtures must have exactly `aletheia`, `eidos`, `risk`, `reason`; stored `risk`/`reason` are snapshots recomputed by `policy.classify()` and only consistency-checked by tests.
- Live mode requires `DETECTIF_API_KEY`; `DETECTIF_BASE_URL` defaults to `https://platform.detectif.ai`. Core live verification makes one combined `/v1/speakers/verify` call with `detect_deepfake=true`. `DetectifClient` rejects `.webm` locally although core upload storage accepts it.
- `REVIEWER_API_TOKEN`, when set, requires `Authorization: Bearer ...` only on employee create/enroll and approval decide. The bot's localhost live-state bridge uses separate `BOT_INTERNAL_TOKEN`.
- `DATABASE_PATH`/`UPLOAD_DIR` default under gitignored `backend/data/`; CORS defaults to localhost:3000/:5173; TTL defaults are 300 s challenge and 120 s approval (clamped 30–3600/10–600). Seeded IDs: `emp_rahul` requester, `emp_priya` approver, `emp_carol` security_admin.

## Domain invariants
- Fail closed: hard `FAIL` can never be approved or executed; `BORDERLINE` requires peer review; missing/errored model signals are never `PASS`.
- Approvals are SHA-256 action-hash-bound, number-matched, different-person, one-time, and expire on the 120 s default TTL; execution is one-time and explicitly simulated; audit events are hash-chained and tamper-evident.

## Toolchain quirks
- `vite.config.ts` has a build-only plugin copying `src/assets` to `dist/src/assets`; keep it. `DISABLE_HMR=true` disables Vite HMR and file watching (AI Studio behavior).
