# AGENTS.md

## Layout
- `src/` is an AI Studio Vite/React 19 UI; `App.tsx` is wired to `src/lib/voiceGateEngine.ts`. Default "Demo Engine" reimplements the workflow in-browser; "Live FastAPI" calls `backend/app/main.py` (default `http://127.0.0.1:8000`, override with `VITE_API_BASE_URL`).
- Backend logic: `backend/app/approval/` (workflow), `ai/voice_check.py` + `ai/detectif_client.py` (Aletheia/Eidos), `core/` (config/DB/storage), `audit/` (hash chain). Mirror workflow changes in both `src/lib/voiceGateEngine.ts` and `backend/app/approval/*`.
- `backend/app/bot/` is a separate Telegram long-polling path: Aletheia-only, ffmpeg to 16 kHz mono WAV, 4–120 s/20 MB limits, 5 checks/10 min/user; bot fixture mode reuses `backend/fixtures/verify_*.json` but reads only `aletheia`.

## Commands
- Frontend: `npm ci`, `npm run dev` (port 3000, host 0.0.0.0), `npm run build`, `npm run lint` (= `tsc --noEmit`; no ESLint/test script).
- Use `.venv/bin/python`; system `python3` lacks backend deps. Backend has no full Python manifest: core needs `fastapi`, `uvicorn`, `httpx`, `pytest`, `python-dotenv`; bot needs `requirements-bot.txt` plus system `ffmpeg`.
- API from repo root: `.venv/bin/python -m uvicorn backend.app.main:app --port 8000`.
- Tests from repo root: `.venv/bin/python -m pytest backend/app -q`. Import fallback (`backend.app.*` ↔ `app.*`) makes cwd matter; tests use tmp SQLite/`httpx.MockTransport`, no external services.
- Current baseline: only `backend/app/test_backend_core.py::test_fastapi_full_workflow_and_reviewer_token` fails because `/health` now also returns `ai_mode`; other 47 tests pass.
- Bot: `.venv/bin/python -m backend.app.bot.bot` (long polling; needs `TELEGRAM_BOT_TOKEN`).
- Enrollment CLI: `.venv/bin/python -m backend.app.ai.enrollment <employee_id> <audio_paths...>`; it forces live mode, so `DETECTIF_API_KEY` is required even when `AI_MODE=fixture`.
- Live raw Detectif capture: `.venv/bin/python -m backend.app.ai.capture_fixtures <wav_path> --name <label>` → `backend/fixtures/raw_<label>.json`.

## Config and environment
- `.env` is loaded from repo root, `backend/`, then CWD in that order with `override=False`, so the first definition of a variable wins. `.env*` is gitignored except the tracked `.env.example`.
- `AI_MODE` defaults to `fixture`; `FIXTURE_SCENARIO` (`pass|borderline|fail|error`) selects `backend/fixtures/verify_<scenario>.json`. Core fixtures must have exactly `aletheia`, `eidos`, `risk`, `reason` or verification fails closed.
- Live mode requires `DETECTIF_API_KEY`; `DETECTIF_BASE_URL` defaults to `https://platform.detectif.ai`. Core live verification makes one combined `/v1/speakers/verify` call with `detect_deepfake=true`.
- `REVIEWER_API_TOKEN`, when set, requires `Authorization: Bearer ...` on employee create/enroll and approval decide only.
- `DATABASE_PATH`/`UPLOAD_DIR` default under gitignored `backend/data/`; CORS defaults to localhost:3000/:5173; TTL defaults are 300 s challenge and 120 s approval. Seeded IDs used by tests/manual flow: `emp_rahul` requester, `emp_priya` approver, `emp_carol` security_admin.

## Domain invariants
- Fail closed: hard `FAIL` can never be approved or executed; `BORDERLINE` requires peer review; missing/errored model signals are never `PASS`.
- Approvals are SHA-256 action-hash-bound, number-matched, different-person, 120 s TTL, and one-time; execution is one-time and explicitly simulated; audit events are hash-chained and tamper-evident.

## Toolchain quirks
- `vite.config.ts` has a build-only plugin copying `src/assets` to `dist/src/assets` so generated root-relative image URLs still resolve; do not remove it.
- `DISABLE_HMR=true` disables Vite HMR and file watching (AI Studio behavior).
