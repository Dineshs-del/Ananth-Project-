# Status

## Live smoke hardening
- `backend/scripts/live_smoke.py` now strips header/API-key-like fields recursively from the raw verify JSON before saving `backend/fixtures/raw_<label>.json`; stdout still prints only parsed `aletheia`/`eidos`/`risk`.
- Added mocked `app/ai/test_live_smoke.py` covering exactly one combined verify call, sanitized saved JSON, no key in output, and rejection of `AI_MODE=fixture`.
- Suite after this step: 83 passed, 1 failed (known stale `/health` assertion).

## Pytest collection
- Added `backend/pytest.ini` (`testpaths = app`, `pythonpath = ..`, registered `asyncio` marker) and `backend/conftest.py` aliasing `app` to `backend.app`, with package `__init__.py` files, so `cd backend && python -m pytest` collects every suite with consistent imports.
- Verified from both `backend/` and the repo root: 81 passed, 1 failed (the known stale `/health` assertion), no collection errors.

## Python requirements
- Added `backend/requirements.txt` with all backend, bot and test dependencies, including `pytest-asyncio`; `requirements-bot.txt` now delegates to it with `-r backend/requirements.txt`.
- Verified both requirements files install/resolve cleanly.

## Dependency cleanup
- Pinned `vite` 8.3.8 and `esbuild` 0.28.2 so plain `npm install`/`npm ci` resolve without ERESOLVE; regenerated `package-lock.json`.
- Removed unused `@google/genai`, `express`, `@types/express`, `dotenv`, `tsx` and the stale `server.js` reference from `clean`; dropped template `GEMINI_API_KEY`/`APP_URL` from `.env.example`.
- Verified `npm install`, `npm ci`, `npm run lint` and `npm run build` all succeed.

## Fixture policy consistency
- Branch: `fix/eidos-fixtures`
- Fixture voice checks now recompute `risk`/`reason` with `policy.classify()`; stored fixture risks are covered by a consistency test.
- `verify_pass`/`verify_fail` include realistic Eidos signals; `verify_borderline` keeps Eidos `null` so the fail-closed policy still returns `BORDERLINE`; `verify_error` is unchanged.
- Tests: `backend/app/ai/test_policy.py` passes (47 tests). Broader suite has unrelated failures: async alert tests need `pytest-asyncio`, `/health` still expects no `ai_mode`, and `live/test_live_guard.py` fails collection on a relative import.

## Live smoke script
- Added `backend/scripts/live_smoke.py`; usage: `python -m backend.scripts.live_smoke <wav_path> --label <label> [--speaker-id <id>]`.
- Requires `AI_MODE=live`; makes at most one enroll call (skipped when the local DB already reports enrolled samples) and exactly one verify call, with retries disabled.
- Saves the raw verify JSON to `backend/fixtures/raw_<label>.json`, then prints parsed `aletheia`/`eidos`/`risk`; API keys are never printed.
- Not executed as part of this change.

## AGENTS.md refresh
- Updated `AGENTS.md` against the current branch: corrected the test baseline (full root run stops on 2 collection errors; ignoring those gives 1 failed, 74 passed), and recorded the `live_smoke` command, fixture `risk`/`reason` recompute, `.webm` rejection, and bot BORDERLINE fixture special case.
- No workflow, fixture schema, or architecture changes.

## Live-mode edge-case hardening
- Removed `.webm` from `DetectifClient` supported formats; browser-recorder WebM now fails locally with `unsupported_format`.
- Added stdlib `wave` duration validation for WAV uploads outside 4-120s; non-WAV duration still relies on the API because no decoder/`ffprobe` dependency is assumed.
- Mapped HTTP 402, 429/known rate-limit codes, timeouts, and 404/known speaker-not-found codes to clear `DetectifError` messages/codes.
- `voice_check.verify_voice()` now includes the typed error message in its fail-closed `BORDERLINE` reason and chooses a relevant next step; API keys are never included.
- Added mocked `httpx.MockTransport`/monkeypatch tests for 402, 429, timeout, WebM, short/long WAV, and unenrolled speaker; also fixed `live_smoke.py` to stop passing an unsupported `timeout_seconds` argument.
