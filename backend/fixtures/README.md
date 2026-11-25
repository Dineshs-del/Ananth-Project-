# Voice Gate AI Fixtures

All `verify_*.json` files below are **sample** fixtures built from the documented Detectif API response shape for Day-1 integration (`AI_MODE=fixture`). They are not yet calibrated from live audio captures.

## Agreed Output Schema (`verify_voice`)

Every `verify_*.json` file contains **exactly** these four keys:

- `aletheia`: `{"verdict", "score", "duration_seconds", "operation_id"}` or `null`
- `eidos`: `{"decision", "similarity", "duration_seconds", "operation_id"}` or `null` when the speaker check is unavailable
- `risk`: `"PASS"` | `"BORDERLINE"` | `"FAIL"`
- `reason`: plain-English explanation noting that a score is evidence, not proof, plus a concrete next step

Stored `risk`/`reason` are snapshots: `_load_fixture()` recomputes both with `policy.classify()`, and `test_policy.py` checks that every fixture's stored `risk` still matches.

## Sample Files

| File | Status | Scenario (`FIXTURE_SCENARIO`) | Description |
| --- | --- | --- | --- |
| `verify_pass.json` | sample | `pass` | Aletheia `"real"` low-risk result plus Eidos `"match"`, returns `PASS`. |
| `verify_borderline.json` | sample | `borderline` | Aletheia `"real"` near cutoff with Eidos unavailable (`null`), returns `BORDERLINE` with re-record/callback next step. |
| `verify_fail.json` | sample | `fail` | Aletheia `"fake"` synthetic speech indicator plus Eidos `"no_match"`, returns `FAIL` with callback next step. |
| `verify_error.json` | sample | `error` | Simulated API/audio error (`aletheia: null, eidos: null`), fails closed as `BORDERLINE` (never `PASS`). |

## Live Raw Captures

Running `python3 -m backend.app.ai.capture_fixtures <wav_path> --name <label>` writes the raw Detectif JSON response to `raw_<label>.json` in this folder. No API keys or request headers are stored.
