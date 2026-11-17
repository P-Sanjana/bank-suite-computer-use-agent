# Computer-use automation (interface.ai take-home)

LLM-driven **discovery** records a flow against a local bank back-office UI. The recording is a versioned **capability artifact**. **Replay** runs that artifact with no model in the loop.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # Linux / macOS
pip install -r requirements.txt
playwright install chromium
copy .env.example .env          # Windows; set ANTHROPIC_API_KEY
cp .env.example .env            # Linux / macOS
```

Serve the target app (Bank Suite) in another terminal:

```bash
python -m http.server 3000 --directory target-app
```

Discovery needs a live Anthropic key. Replay and tests do not.

## Demo path

1. Discover (real LLM run):

```bash
python scripts/run_discovery.py --headful --goal 'Look up member 1042381 and read their credit score' --entry http://localhost:3000/member-lookup.html
```

Use **single quotes** around `--goal` when the text includes `$`. Discovery also restores that case if it still happens.

If the agent escalates, keep `--headful`: a collapsed **ESCALATED** dock appears at the bottom of the same window. Use the page, expand the dock, enter a required description, then **Resume** or **Stop**. Response JSON is written under `evidence/<run>/interventions/` and `handoff.json`. `--mock-operator` skips the dock and resumes (for tests).

2. Replay (no LLM):
For member lookup and declining the credit card application

```bash
python scripts/run_replay.py --artifact artifacts/<the-file>.json --param member_no=1042381
python scripts/run_replay.py --artifact artifacts/review-application-v1-<id>.json --param app_id=APP-1001187 --param decision=Decline --param decline_reason="Does not meet product policy" --param officer_notes="DTI above policy"
```

Not-found business outcome (legitimate result, exit code 2):

```bash
python scripts/run_replay.py --artifact artifacts/<the-file>.json --param member_no=8888888
```

## Tests (no API key)

```bash
pytest -q
```

## Layout

- `target-app/` - hostile local Bank Suite (tables, unlabeled inputs, iframe detail)
- `src/discovery/` - observe → plan → act → record
- `src/surface/locator.py` - shared 9-step locator chain (plus value-cell proximity for reads)
- `src/schema/` - artifact + result contracts
- `src/replay/` - deterministic runner
- `src/escalation/` - live-session pause / file-based operator handoff
- `src/safety/` - allowlist + redaction
- `artifacts/` and `evidence/` - saved capabilities and run logs

Each discovery/replay folder includes **`steps.json`** (actions taken) and discovery also writes **`step-log.json`** (observe/reason/action turns). Screenshots sit beside those files.
