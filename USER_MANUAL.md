# JAMES user manual

JAMES guides a technician through a repair without touching a screen.
**Point** at the machine, **ask** out loud, **pinch** to confirm each step.

## Before you start (every time)

1. **Terminal tab 1:** `ollama serve` (leave it running; "address already in use" also means it's running).
2. **Terminal tab 2:** `cd ~/Downloads/james-app && source .venv/bin/activate && python app.py`
3. **The machine tag:** `assets/asset_tag_P-3.png` (ArUco id 4; the old id 3 card is the owner key now) full screen on a phone at full brightness, or the printed PDF.
4. **Click once inside the JAMES window** so it receives key presses.

## The screen

- **Top bar:** the current state (LOCKED, TARGET_PENDING, SAFETY_BLOCKED...), CLOUD OFF, DATA: SAMPLE, and which engines are live
  (ECHO: the model or keyword rules; PAGE: BM25 + Qdrant; FOREMAN: LangGraph).
- **Left panel:** what to do next (amber), the current step with its sources, and red when WARDEN blocks a step.
- **Camera view (mirrored like a selfie):** blue outline on the tag, amber ring when you point at it, white dot on your fingertip,
  and a ring that fills while you pinch.
- **Bottom right:** short messages (green = done, amber = info, red = refused, with the reason).

## Gestures

| Gesture | How | Means |
|---|---|---|
| Point | Index finger toward the tag, or touch the tag with your fingertip. Hold still for half a second. | "This machine" |
| Pinch | Touch thumb tip to index fingertip and hold until the ring turns green (0.6 s). Release before the next pinch. | "Yes / confirm" |

Keep your hand inside the camera view, about 30 to 60 cm away, palm facing the camera.

## Keys

| Key | Does |
|---|---|
| **U** | Unlock / lock (presenter). Locking resets the job. |
| **V** | Start talking; **V** again to stop. |
| **1** | Backup: sends "Pump 3 is vibrating more than usual" (shown as SCRIPTED INPUT). |
| **T** | Confirm the machine when JAMES asks "Is this P-3?" |
| **C** | Backup confirm when the pinch won't register (logged as a key confirm). |
| **S** | Skip the current step (logged as skipped, never as done). |
| **L / Z / P** | Confirm lockout / zero energy / PPE by key. |
| **E** | Escalate to the senior technician (writes a ticket in `records/`). |
| **A** | Senior approves the last finished job, so JAMES can reuse it. |
| **R** | Start a new job. |
| **Y / N** | Answer FOREMAN's question (pinch also means yes). |
| **O** | Sensor log on/off (demo): with it off, FOREMAN has to ask you instead. |
| **Q** | Quit. |

## One full run (90 seconds)

| # | You do | You see | Say to the judges |
|---|---|---|---|
| 1 | **U** | "Point at the machine's asset tag" | "JAMES starts locked. Nothing happens until the presenter unlocks." |
| 2 | Point at the tag | Ring on P-3, "P-3 confirmed (asset_tag)" | "LENS reads the asset tag. The tag decides the machine, not a guess." |
| 3 | Pinch | "Recorded: ppe_gloves, ppe_eye" | "PPE confirmed with a pinch." |
| 4 | **V**, "Pump 3 is vibrating more than usual", **V** | What it heard, then STEP 1 OF 5 with Source, Sensor evidence, Similar case | "ECHO understood me. FOREMAN asked PAGE, PULSE and LEDGER in parallel. Every line has a source." |
| 5 | Pinch | Step 1 logged, Step 2 appears | "One step at a time, each confirmed by a human." |
| 6 | **S**, **S** | Step 4 in red: BLOCKED BY SAFETY POLICY, LOTO-01, ZERO-01 | "We skipped lockout on purpose. WARDEN is plain code, not AI. The model can't argue with it." |
| 7 | Pinch, pinch | "Lockout applied?", then "Zero energy verified?", then the step is allowed | "Once a human confirms isolation, WARDEN checks again." |
| 8 | Pinch, pinch | "Job complete. Repair record: records/..." | "The repair record the plant must keep is written for them." |
| 9 | **A**, **R**, point, **1** | Similar case now shows job J-1xx | "A senior approved it, so JAMES learned it. Only approved fixes are reused." |

## If something goes wrong

| Problem | Do this |
|---|---|
| Keys do nothing | Click inside the JAMES window. |
| No blue outline on the tag | Brighter phone screen, less glare, hold it flatter and closer. |
| Pinch doesn't fill the ring | Keep the whole hand in view. Use **C** as backup. Raise `PINCH_RATIO` in `config.py`. |
| "Didn't catch that" / wrong words | Press **V** and speak closer, or press **1**. |
| ECHO shows "keyword rules" | Ollama isn't running: start `ollama serve` in tab 1. JAMES still works. |
| "Step was stale" | You waited over 90 s. Pinch again: WARDEN re-checks first. |
| Stuck anywhere | **R** for a new job, or **U** twice. |
| Camera or mic refused | System Settings, Privacy & Security, Camera / Microphone, turn on Terminal, run again. |

## Reset between rehearsals

Quit with **Q**. To clear the learned jobs, delete `data/james.db`. Speeds for the "under 10 s" claim are in `data/runs.csv`.

## The adaptive part (iteration 2): show this after the run above

FOREMAN no longer does one fixed pass. After PAGE, PULSE and LEDGER answer, it picks the next action from the
ones that are valid at that moment, and the left panel shows each round and **who decided it** (the local
model, or the rules when the model is off).

| # | You do | You see | Say to the judges |
|---|---|---|---|
| 10 | **O** (sensor log off), **R**, point, **1** | "FOREMAN needs one answer from you. Is the bearing housing hotter than usual?" | "No sensor log, so the evidence can't separate bearing wear from misalignment. It asks one question, taken from the manual's table, instead of guessing." |
| 11 | **N** | Misalignment steps (p. 6), "Sources disagree: bearing wear vs misalignment" | "My answer changed the conclusion. The old approved job disagrees, and JAMES shows that instead of hiding it." |
| 12 | **O**, **R**, point, **V** "Pump 3 is leaking" **V** | "No conclusion. Press E to escalate." and three search rounds | "The manual has nothing on leaks. It tried two narrower searches, then stopped. No invented procedure." |

For a written trace with the live local model (to see every FOREMAN decision): `python -m tools.maintenance_trace`
(writes `records/trace_<time>.md`). `python -m tools.build_id` prints the build id to quote with it.

## JAMES's voice (james.py)

- **On/off:** the **VOICE** switch at the top of the screen, next to the mode pill. Pinch it, click it, or press **M**.
  "Be quiet" / "talk to me" do the same. It is remembered next time (`data/prefs.json`).
- **The voice:** a neural voice that runs on this Mac (Kokoro-82M, offline). Default: `bm_fable`, a calm British
  male voice. If the neural voice is not installed JAMES falls back to the best male macOS voice.
- **Install once:** `pip install kokoro-onnx==0.6.1` (the voice files are already in `assets/voice/`;
  `python -m tools.get_voice` checks them against pinned hashes).
- **Pick by ear:** `python -m talk --audition` (plays each male voice), then `python -m talk --use bm_george`
  (or `bm_fable`, `bm_lewis`, `mix_fable_lewis`, `am_michael`, `am_onyx`).
- **Stepping away:** if you start talking with the V sign and your hand leaves the camera before you close it,
  JAMES shows what it heard and waits for "yes" instead of running it.
- **Unclear speech:** anything heard below 0.5 confidence waits for a clear "yes" (only "pause" runs straight away).

## Opening websites by voice

- **"Open ChatGPT on Chrome"** opens chatgpt.com in Chrome, never the ChatGPT desktop app. The same works for any
  known site (IMDb, Netflix, YouTube, GitHub, ...) and for Safari, Brave, Firefox, Arc and Edge.
- A name JAMES doesn't know ("open blue harbour tiles on Chrome") becomes a Google search in that browser, and
  JAMES says it searched. Heard unclearly, that waits for "yes" first.
- **"Open asphalt.com"** opens that address in your default browser.
- **Longer requests** worked out by the local model may only use sites you named. If the plan uses a site you
  didn't mention (for example Amazon when you said IMDb), JAMES runs nothing and says why.
