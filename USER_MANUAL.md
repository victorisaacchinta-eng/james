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

**Inside ATLAS (X)** the hand works the drawing instead, and nothing can be confirmed until you close it:

| Gesture | Does |
|---|---|
| Open your hand slowly | Explode the machine: the explode follows how open your hand is |
| Close your hand | Assemble it (the same control, at zero) |
| Move or twist an open hand | Turn and tilt it; twisting rolls it |
| Point (index finger only) | Light up the part under your fingertip; its card shows which WARDEN rules guard it |
| Pinch a part | Pull it out and drag it; let go and it springs back, still picked |
| Pinch empty space | Clear the pick |
| Two hands, apart or together | Zoom in or out |

The strip at the bottom of the ATLAS panel is the legend: the gesture ATLAS sees right now is lit amber. The ring
around your hand grows as you open it; its amber arc is the explode level.

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
| **X** | ATLAS on/off: the exploded view of the targeted machine. While it is open, C, T, L, Z, P, Y and N do nothing. |
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
| "ATLAS is open: close it (X)" | You pinched or pressed a confirm key inside ATLAS. Press **X**, then confirm. |
| ATLAS lines shake or lag | Tune `SMOOTH_MIN_CUTOFF` (lower = steadier) and `SMOOTH_BETA` (higher = less lag) in `config.py`. `REDUCED_MOTION = True` turns off all animation. |
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

## The ATLAS app (james.py, or `python atlas_app.py`)

Open your palm for the launcher and pinch the **ATLAS** tile, or say "open atlas". ATLAS fills the window; the
launcher and the voice gestures are off until you close it.

The sidebar on the left is the legend. The gesture ATLAS sees right now is lit amber, so you always know what
it thinks your hand is doing.

| Gesture | What happens |
|---|---|
| Open slowly | Explode it: the explode follows how open your hand is, 0 to 100% |
| Close your hand | Pull it back together |
| Move or twist (open or closed hand) | Move: turn and tilt it. Twist your wrist: roll it |
| Point (index finger only) | The part under your fingertip lights up; the sidebar card says what it is |
| Pinch a part | Pull it out and drag it. Let go: it springs back into place and stays picked |
| Pinch empty space | Clear the pick |
| Two hands: apart / together | Zoom in / out (0.4x to 6x) |
| Peace sign, held half a second | Next model (the cyan arc on the ring fills while you hold) |
| Quick pinch on a tile in the bottom shelf | That model loads, exploded |
| Quick pinch on **Reset view** / **Close** | Back to the start view / leave ATLAS |

**Colours.** Each part is coloured by family: wheels and tyres saffron, body and structure steel blue, power and
drive coral, sensors and signal green, mechanisms rose, glass and optics sky blue, interior and soft parts tan. The
key is at the bottom right; the picked part's card names its family. Pointing at or picking a part lightens it and
dims the rest. The ring under the model is a turntable that turns with it, and while exploded a dashed line runs
from each part back to where it sits assembled. (In the maintenance app parts stay grey: there, colour means
safety.)

**Heat and frame rate.** The FPS is shown top right. ATLAS draws at most 30 frames a second, looks for a hand on
only every 3rd frame once none has been seen for 3 seconds, redraws the sidebar and shelf only when they change,
and on a machine too slow to redraw everything while the model moves, draws fewer lines until it stops. The
settings are `ATLAS_MAX_FPS`, `ATLAS_IDLE_AFTER_S` and `ATLAS_FRAME_BUDGET_MS` in `config.py`.

Tips: a resting hand never turns anything (it has to move a few pixels first). Keep your hand open while you
turn the model if you want it to stay exploded. Pointing, pinching and the peace sign freeze the explode, so
curling your fingers to point does not collapse it. Every threshold is in `config.py` (`ATLAS_*`).

Keys inside ATLAS: **A D** turn, **W S** tilt, **+ -** zoom, **E** explode or assemble, **N B** next or previous
model, **Tab** next part, **R** reset, **Esc** close (in `atlas_app.py`, Esc or Q quits). In owner-key mode your
second hand counts as "another hand": ATLAS reads it only for two-hand zoom, never to pick or launch anything.

## CANTUS, the rhythm game (james.py, or `python cantus.py`)

Open your palm for the launcher and pinch the **CANTUS** tile, or say "open cantus".

| Do | What happens |
|---|---|
| Point at a song card and pinch | The song starts after a 3-2-1 |
| Pinch Easy / Normal / Hard | Changes the difficulty (shown on every card) |
| Swing through a block the way its arrow points | PERFECT, GOOD or OK by timing; the block splits |
| Swing the wrong way, or with the wrong hand | BAD: the combo resets |
| Let a block pass | MISS |
| Peace sign, held almost a second | Pause; pinch Resume or Songs |
| Pinch Close on the song list | Back to JAMES |

Red blocks are for your left hand, blue for your right. Alone with one hand, colours don't count. A dot means
any direction. Swing from the elbow, not the wrist: a slow drift through a block does nothing.

**Your own songs:** put MP3, M4A or WAV files in `data/cantus/songs/` (in the james folder) and press **R** on
the song list. The card says "Mapping the beat..." for a few seconds, then it is ready. The map is saved, so the
next time it is instant. Name files "Artist - Title.mp3" and the card shows both.

**If the blocks and the music don't line up:** press **]** while playing if the blocks arrive before the beat,
**[** if they arrive after. Each press moves them 10 ms. Put the number you like in `CANTUS_AUDIO_OFFSET_MS`
in `config.py`.

Keys: **1-9** play that song, **D** difficulty, **R** rescan your songs, **Space** pause, **Enter** play again
(results), **Esc** back, **Q** quit.

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
