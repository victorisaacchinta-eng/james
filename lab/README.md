# JAMES Workflow Lab

A separate, locally runnable app for the three workflows behind JAMES, without camera, voice or gestures:

1. Retrieve and explain sensor evidence from synthetic logs (PULSE).
2. Answer a maintenance question with cited, step-by-step guidance (PAGE + FOREMAN), checked by WARDEN.
3. Record the work and outcome, have a demo reviewer approve it, and retrieve it on a later similar job (LEDGER).

**Everything is synthetic.** The machines, readings, procedure and history are invented for software testing.
Nothing here is an engineering limit, an OEM procedure, or evidence about real equipment.

It lives inside `james-app/` so it can reuse `james_core` (WARDEN and the pack loader) read-only.
It does not change the existing JAMES app. Its machine knowledge ships as a pack: `packs/workflow-lab-pumps/`.

## Run it (fixture mode, offline)

One-time, from `~/Downloads/james-app` with the venv active:
```
pip install "fastapi>=0.110,<1" "uvicorn>=0.29,<1"
```
Then:
```
python -m lab
```
Open http://127.0.0.1:8765. The ledger is `lab/data/lab.db` and survives restarts.
`python -m lab --reset` moves it aside (kept as `lab.db.bak-<time>`) and starts empty.

Optional local AI: `LAB_MODE=local_ai python -m lab` (needs `ollama serve` and the model in `LAB_OLLAMA_MODEL`,
default `qwen3-vl:4b`). You can also pick the mode per job in the Workspace. If the model fails, the Lab says so and
offers fixture mode; it never swaps in scripted output silently.

## Demo script (about 3 minutes)

| # | Do | Shows |
|---|---|---|
| 1 | Workspace: scenario "Abnormal", asset DEMO-PUMP-03 | Synthetic-data, fixture-mode and pack badges; scenario clock |
| 2 | Keep the first example question, press **Start job** | Job created, evidence retrieved, guidance produced |
| 3 | Evidence screen | Vibration, bearing temperature and current up; flow down; baseline vs current with charts; click **Source rows** |
| 4 | **Guided work** | Observed / Possible explanation / Unknown / Next step. Two *possible* causes, never confirmed. Seeded history listed as *not recommended*, with reasons |
| 5 | Click step **PV-06** in the list, press **I have read this** | WARDEN refuses: ISO-01, PPE-01 (needs information). The refusal is recorded |
| 6 | In Prerequisites, **Record attestation** for isolation and PPE | PV-06 turns allowed. PV-08 stays **blocked** (67 C is above the 55 C demo touch limit) |
| 7 | PV-06: **I have read this**, then write a note and **Report work**; close as *reported resolved* with a summary | Read, report and close are separate events |
| 8 | Switch to **Reviewer** (top right), Review queue, pick the job, give a reason, **Approve for reuse** | Clearly labelled demo role, not authentication |
| 9 | Switch back to Technician, Workspace, **Start job** again with the same question | Guided work now shows the approved job under *Recommended experience*, with why it matched |
| 10 | Work ledger: pick the first job, open **Markdown export**; or open Developer | The full record: sources, hashes, policy decisions, timings |

Short demo version: run steps 1 to 8 once before you present, then show 9 and 10 live, and say the earlier job was done
before the talk.

Other scenarios to show on request: *Missing data* (bearing temperature offline, so PV-08 asks for a reading instead of
assuming), *Stale data* (newest reading is 3 h older than the scenario clock), *Invalid data* (file rejected whole,
nothing stored), *Ambiguous* (borderline vibration, no cause offered), asset DEMO-FAN-01 (no pack: escalate, no steps),
and "How do I update the flow meter firmware?" (unsupported: clarify or escalate).

## Tests

```
python -m pytest -q lab/tests                       # 27 tests: PDD A01-A18 (A15 covers the failure path only) plus structural checks
python -m james_core.pack check packs/workflow-lab-pumps   # WARDEN seeded cases for the Lab pack
python -m lab.fixtures                              # regenerate the CSVs (byte-identical; A01 checks it)
```

## How it is built

| Module | File | What it does |
|---|---|---|
| PULSE | `pulse.py` | Validates the whole CSV (schema, timestamps, units vs the pack, N/A channels), stores it by sha256, computes baseline vs current per channel for one asset. No AI |
| PAGE | `page.py` | Versioned Markdown with stable section ids; step catalog checked against the document; keyword retrieval filtered by asset and category |
| FOREMAN | `foreman.py` | Fixture guidance from the pack's pattern table; optional Ollama provider; **validation** of every step id, evidence id and number |
| WARDEN | `warden.py` | Adapter over `james_core.safety_guard`: the same deterministic engine as the main app |
| LEDGER | `store.py`, `service.py` | SQLite with append-only triggers, idempotency keys, corrections as new events, review/revoke, reviewed-reuse search, exports |
| API | `api.py` | FastAPI, thin. All rules live in `service.py`, so a client that skips the UI meets the same checks |
| UI | `static/` | No build step. Six screens: Workspace, Evidence, Guided work, Work ledger, Review queue, Developer |

Main-app seam (documented, not built): the camera app could call `POST /api/jobs`, `GET .../evidence`,
`POST .../guidance`, then `.../acknowledge` on a pinch and `.../report` on a spoken "done". The pinch gate would map to
acknowledge; WARDEN decisions would come back exactly as the Lab shows them.

## Completion report (25 Sep 2026)

**Built and tested:** separate app; seeded, reproducible fixtures (7 variants: 5 CSVs, a stale clock, seeded conflicting
history); asset and window filtering; evidence summaries with row-level citations; Markdown procedure retrieval; fixture
mode; WARDEN checks that block only dependent steps; prerequisite attestations scoped to job, asset and guidance version;
separate read / report / close events; idempotent submissions; persistence across restarts; outcomes; demo review with
approve, reject and revoke; reviewed repeat-case retrieval with reasons for exclusion; Markdown and JSON exports; developer
panel with real timings and live policy-test results; a local Ollama adapter with validation and honest failure.

**Measured** (cloud Linux container, not your Mac; measure again there before quoting): evidence 4 to 18 ms, fixture
guidance about 3 ms, 6/6 step citations resolve, WARDEN seeded cases 10/10 (7/7 violations blocked, 0/3 false blocks).
The PDD targets (evidence under 2 s, local-AI proposal under 10 s in 8 of 10 warm runs) are **not measured yet on the Mac**.

**Not done or not verified:**
- Local AI mode has not been run against a live Ollama model. Only the failure path is tested (A15).
- CSV upload through the UI (a Should) is not built; datasets come from the fixture files.
- The browser walk-through was run once with a headless browser during the build; it is not an automated test in this repo.
- Demo roles are not authentication. The append-only log is an application rule, not a tamper-proof store.
- Seeded demo history is global (visible in every scenario), and labelled as seeded everywhere it appears.
- No field validation, no real safety verification, no plant readiness. No downtime, accuracy or injury-prevention result is established.
