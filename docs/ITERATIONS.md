# How JAMES was built: iterations

JAMES was built in short iterations. Each one followed the same loop:

1. **Review** the build and list findings: **P0** (must fix before a live demo), **P1** (should fix), and
   **ST-01 to ST-20**, twenty scenario tests that describe behaviour end to end.
2. **Fix** each finding and add a regression test for it.
3. **Run it live** on a MacBook Air M4. Every sentence that misbehaved in the app log became a new test, and the
   whole log was replayed through the old and new code to check that nothing else changed.

Code comments refer to these ids (for example "iteration 2, P0-E" or "iteration 3, item D"), so each rule in the
code can be traced back to the reason it exists.

**Evidence levels used below**

- **LIVE:** seen working on the Mac, in the app log or on screen.
- **TESTED:** covered by automated tests (camera, mic, Ollama, `say` and Chrome are faked in tests; the suite
  refuses all network connections).
- **UNVERIFIED:** built, with no live evidence yet.

Build ids come from `python -m tools.build_id`.

---

## Iteration 1: voice pipeline and the first live runs

- Voice commands v3 to v6: a complexity router (simple asks go to fast rules, long ones to the local model
  planner), a schema-restricted planner, AI-chat prompts (prefill only), Spotify track play, GitHub
  top-repo search, "collect" into Apple Notes, closing apps, message prefill (never sent), and a context resolver
  ("close it", "copy those links", "send it").
- Talkback: JAMES says what it did.
- LIVE: owner key, problems to fixes, Spotify track play and GitHub search, local planner (3.7 s), collect to Notes,
  talkback.
- Defects from the live log became `tests/test_defects_v7.py` (14 tests).

## Iteration 2: hardening (P0-A to P1-F)

| Finding | What changed | Status |
|---|---|---|
| P0-A low confidence | Below 0.5 confidence JAMES shows what it heard and what would run, and waits 12 s for a clear "yes". The model is never asked below 0.5. Stop, cancel and mute stay immediate. | TESTED |
| P0-B intent | Negation ("do not send it"), questions ("explain how to close an app") and quoted text never become actions. Partial plans name each part that was not done. | TESTED |
| P0-C send | Asking ChatGPT or Claude only fills the box (`ASK_AUTO_SEND = False`). "Send it" presses send only in the Chrome tab that holds the exact prompt JAMES prepared. | TESTED; Chrome selectors UNVERIFIED |
| P0-D egress | One gate on every outbound path (`james_core/egress`). The maintenance app and the Lab refuse all consumer web tools; the model must be local. | TESTED |
| P0-E agents | FOREMAN became a loop: fan-out to PAGE, PULSE and LEDGER, then one valid action per round (`page_search`, `page_check`, `ask`, `finish`, `abstain`). | TESTED |
| P0-F microphone | The device is always released; opens, closes and errors are logged; `tools/mic_check`; typed input with `/`. | TESTED |
| P1-A consumer repairs | Generic fixes are labelled "not an approved or OEM procedure". | TESTED |
| P1-B owner | A command is dropped if another hand takes control while it is recorded. | TESTED |
| P1-C markers | `tools/make_marker_sheet` prints the owner key and every pack tag; the owner key is never targeted as a machine. | TESTED |
| P1-D sources | Past jobs are used only if approved, not revoked, same equipment class and recent; memory never decides a cause alone. | TESTED |
| P1-E pack | Pack files are read once, hash-checked and served from memory; a snapshot hash is stored with every job. | TESTED |

Scenario tests ST-01 to ST-20 live in `tests/test_iter2_*.py` (voice, egress, FOREMAN, owner, microphone, pack).
ST-12 (restart) is a deliberate cancellation, not a resume.

## Iteration 3: confirmation, budgets and grounding

| Item | What changed |
|---|---|
| A. Confirmation | Every uncertain action waits for a short, clear "yes" from the same owner, on the same screen, within 12 s. Only "pause" runs straight away. |
| B. Decision origin | Each FOREMAN round records whether the model or the rules decided, and why a fallback happened. |
| C. Coordinator | Described precisely: one coordinator with specialist tools. |
| D. Budgets | 6 rounds, 6 tool calls, 60 s, a cancel flag, and a stop on repeated actions. Late results for an ended job are ignored. |
| E. Egress | Exact host parsing, every redirect re-checked, the model endpoint pinned to localhost. |
| F. Send contract | The tab URL and box text are re-checked at the moment of the click; one attempt, never retried. |
| G. Grounding | Only real manual rows confirm a cause; only numbered step lines become steps, word for word, each with its page. |
| H. Snapshot | A full investigation opens no pack file after start-up. |
| I. Departure | If the hand leaves while talking, the command waits for a "yes". |

Also in this iteration:

- **Neural voice:** Kokoro-82M through `kokoro-onnx`, fully offline, sentence by sentence, with a fallback to the
  macOS voice. The fp16 model was silent on 3 of 54 test sentences, so the full-precision model is used.
- **Voice switch:** on/off in the header (pinch, click or M), remembered between runs.
- Tests: `tests/test_iter3_*.py`, `tests/test_voice_neural.py`.

## Iteration 4: live evidence on the Mac

LIVE on the Mac:

- The FOREMAN loop with the real local model (qwen3-vl:4b): 5 of 5 model decisions accepted, 2 to 3 s per round.
  An early version gave up before checking evidence; after the fix it checked the cause and concluded correctly.
- WARDEN kept the coupling-guard step blocked until lockout and zero energy were confirmed.
- Microphone: 15 of 15 cycles clean, with and without the voice talking.

**A crash found and fixed.** Playing neural audio through PortAudio in the same process as the microphone crashed
on macOS. Each sentence is now played by `afplay` in its own process; PortAudio is used only for the mic.

**Fixes from the live run** (`tests/test_live_0420.py`, 13 tests):

- A model plan searched a site the user never named. Model search and collect steps must now name a site the user
  said.
- "Open ChatGPT on Chrome" opens the website in that browser instead of the desktop app.
- Unknown site names become a web search in the named browser, and JAMES says so.
- Empty brand strings from the vision model are cleaned up; fix steps ask for macOS instructions.
- Long first sentences are split so the voice starts sooner.

**Latencies measured live:** Identify 15.0 s, model fix steps 14.1 s, model plans 4.6 to 8.1 s, rule plans under 1 s.

**Testing change:** `tools/simmac.py` runs the whole suite as if on macOS, which caught three tests that only
failed on a real Mac. Every build now passes both runs.

## Iteration 5: ATLAS and steadier hands

Ideas taken from two outside projects, rebuilt for JAMES rather than copied: an exploded 3D view driven by hand
gestures (HoloHand, Subin Raj) and the motion engineering behind a webcam rhythm game (Air Rhythm, Adolph Vijay).

- **ATLAS** (`atlas.py`, `james_core/model3d.py`): X opens an exploded schematic of the targeted machine. The part the
  current step touches is amber, or red while WARDEN blocks the step; a picked part shows a WARDEN preview. Line
  drawing in JSIL (no particles, no glow). The model ships in the pack (`models/centrifugal_pump.yaml`, pack 0.1.2).
- **Safety:** ATLAS owns the hand while open. `App.pinch` refuses while it is open and the confirm keys are ignored,
  so a pinch made to pick a part can never confirm a step. A pinch still held when ATLAS closes must be released.
- **Motion** (`james_core/motion.py`): 1-euro smoothing of the landmarks (2.6x steadier at rest in the test, about
  12 px lag at 1,500 px/s), hands matched frame to frame so filters never mix two hands, the aim point from 120 ms
  before a pinch (closing the fingers drags the tip), and a swipe detector. The launcher in `james.py` uses the
  pre-pinch aim too.
- Tests: `tests/test_atlas.py` (33), `tests/test_motion.py` (12). An independent review found a lock/target
  desync, a pinch-drag read as a swipe, small-window crashes and three weak tests; all fixed with tests that fail
  without the fix. ATLAS draws in about 8 ms a frame on the Linux
  dev box.

NOT yet run live on the Mac: the gestures in ATLAS, the smoothing settings and the swipe thresholds are tested on
synthetic hands only. Tune them from a live run before quoting them.

## Iteration 6: the ATLAS app and its model library

- **ATLAS is an app in the launcher** (`james.py`: the ATLAS tile, or "open atlas") and on its own
  (`python atlas_app.py`). It runs inside the JAMES window; the OS launcher refuses it by design.
- **Seven models, all openly licensed**: Perseverance, Ingenuity and Webb from NASA 3D Resources (public domain);
  a concept car and a chronograph (Khronos glTF Sample Assets, CC BY 4.0); a flight helmet (CC0); and the pack's
  pump. Licences were checked at each source; four older CAD samples (2CylinderEngine, GearboxAssy, Buggy,
  ReciprocatingSaw) were rejected because their licence is not stated.
- **Conversion is offline** (`tools/atlas_import.py`, dev dependencies in `tools/requirements-atlas.txt`): mesh nodes
  are grouped into named parts by rules in `tools/atlas_sources.yaml`, and only feature edges plus a light
  wireframe of each part's shape are kept. The app reads `atlas_library/` with NumPy and hash-checks every file.
- **Controls**: tap-to-pick (a pinch picks on release, so a drag never picks by accident), two-hand pinch for zoom,
  twist and pan, keyboard fallbacks, a shelf of models with thumbnails, Reset and Close tiles.
- **Speed**: drawing is cached; a still view costs about 3 ms a frame, a turning view about 20 ms on the Linux
  dev box (the Mac should be faster; measure it with F telemetry in app.py).
- **Review round** (an independent agent, probes on synthetic hands): a quick pinch missed its part when closing
  the fingers dragged the tip 20 px; the pinch that pressed the launcher tile acted inside ATLAS; two-hand zoom
  jumped on the first frame, spun 180 degrees when the hands swapped roles and ended on a one-frame tracker miss;
  the idle sway redrew every frame (26.6 ms). All fixed: movement counts from the pinch, ATLAS waits for a release
  after it opens, the two-hand gesture has hysteresis, a 150 ms grace and re-anchoring, and the sway redraws in
  0.75 degree steps (4.8 ms a frame). Also: gesture timers reset when ATLAS closes, ATLAS will not open while a
  voice plan is still being worked out, NOTICE.md lists each source URL and copyright year, and the on-screen
  credit wraps instead of being cut.
- Tests: `tests/test_atlas_app.py` (22), more in `tests/test_atlas.py` (36). Live on the Mac: not yet.

## Iteration 7: gestures rebuilt from a live test

First live run on the Mac (screen recording, 5 October 2026): setup passed and every model loaded, but most
gestures felt "inaccurate or very sensitive in the wrong way". The user also sent a recording of HoloHand, the
reference he wants ATLAS to feel like. Watching both frame by frame, the causes were in the design, not the numbers:

- explode and assemble waited for a pose **held still** (palm 0.35 s under 380 px/s, fist 0.3 s): any movement
  reset the timer, so they felt unresponsive, and then fired all at once;
- **a fist read as a pinch**: in a fist the thumb lies on the index finger, so the thumb-index ratio is under the
  pinch threshold; the release of a pinch also looked like a fist and assembled the model;
- **swipes** fired whenever the hand moved quickly into view (90 degrees each time);
- **pinch and drag turned the model**, so a pick that drifted 14 px became a turn;
- two-hand zoom needed **both hands pinching** under a strict ratio, which is hard to hold.

What changed (`james_core/handpose.py`, `atlas_gestures.py`), matching HoloHand's vocabulary:

| Gesture | Command | How it stays steady |
|---|---|---|
| Open slowly / close your hand | Explode follows hand openness, 0 to 100% | Openness = tip-to-wrist over knuckle-to-wrist per finger; dead bands at both ends |
| Move or twist | Turn, tilt; twist rolls | A clutch: the hand must leave a 6 px circle (4 degrees for twist) before anything turns; 0.25 s warm-up when a hand appears; single-frame jumps ignored |
| Point | Highlight the part under the fingertip | Index up, others folded, with per-finger hysteresis |
| Pinch a part | Pull it out, drag it; it springs back | Needs the index half out, so a fist is never a pinch |
| Two hands | Zoom | The second hand must stay 0.2 s; 4% dead zone; a 0.25 s grace for a missed frame |
| Peace, held 0.5 s | Next model | Fires once per hold, 1 s cooldown |

Every pose must be seen for 50 to 150 ms before it counts, and when the hand turns into a point, pinch or V, the
explode and view changes made while it was changing shape are undone. Swipes, hold timers and pinch-drag turning
are gone from ATLAS. The legend (sidebar in the ATLAS app, a strip in the maintenance app) lights the gesture it
sees, and a ring around the hand shows openness, the explode level and the peace hold. The ATLAS app now shows the
camera brighter behind the drawing, like HoloHand.

Review round (an independent agent, probes on synthetic hands): opening a fist still read as a pinch for a few
frames (LENS releases a pinch only past a wider gap), a fist over a shelf tile could press it, a resting or
bystander second hand took over two-hand zoom, the zoom jumped to 6x when the driving hand left mid-zoom, the
turn clutch never let go under 2 px jitter, a new grab inherited the last part's spring offset, and the undo
history outlived Reset and the keys. All fixed, each with a test: a pinch that began in a fist is ignored until
it ends, two-hand zoom needs two open palms and holds still while either is missing, rest is judged against a
small circle over 0.2 s, and history clears on keys, Reset and hand loss.

Tests: `tests/test_atlas_hands.py` (37) feeds clean 21-point hands (the same ones the legend draws) at 30 frames a
second, including 2 px of noise on every landmark: a still hand turns nothing and no pose flickers. Live on the
Mac: not yet.

## Iteration 8: colour families, visual cues, and frame cost

Asked for after the live gesture test: colour the parts (tyres one colour, chassis another), add visual elements,
and mind the Mac's heat and frame rate.

- **Colour families** (ATLAS app only; in the maintenance app colour keeps meaning safety): seven muted hues, each
  part placed by the words in its id and label. Every part of the seven library models is named into a family on
  purpose (a test checks none falls through to the fallback). Pointing or picking lightens the part and dims the
  rest; labels carry their part's colour on the leader; a key sits bottom right; the card names the family.
- **Visual cues that explain something**: a turntable ring under the model that turns and tilts with it (shows the
  rotation), and dashed explode guides from each part's assembled place to where it is now (shows where it came from).
- **Frame cost** (measured on the Linux dev box; the M4 is faster): a still frame went from 17 to 25 ms to about
  4 ms by painting the sidebar and shelf only when they change (they hold about 60 pieces of text) and caching the
  legend's hand drawings. A moving frame costs 17 to 22 ms. If a full drawing costs more than
  `ATLAS_FRAME_BUDGET_MS` on the machine, ATLAS draws every 2nd or 3rd line while the view moves and the full drawing
  as soon as it is still. The app caps at `ATLAS_MAX_FPS` (30) and, with no hand for `ATLAS_IDLE_AFTER_S` (3 s), runs
  hand tracking (the biggest cost) on every 3rd frame only. FPS is shown in the header.

## Iteration 9: CANTUS, a rhythm game in the launcher

Asked for: the "musical thing" from the Air Rhythm post, Beat Saber style, with famous songs playable from the
player's own MP3s and built-in classics that are free to ship.

- **Songs:** four public-domain works (Beethoven, Pachelbel, Mozart) written out as plain melodies and
  synthesised (`james_core/cantus_songs.py`). Famous songs are the player's own files, mapped on their Mac and
  never committed (`data/cantus/` is git-ignored). The national anthem was left out on purpose: a scored game
  with misses is the wrong setting for it.
- **Maps** (`james_core/beatmap.py`): spectral-flux onsets, autocorrelation tempo, onsets snapped to the beat
  grid, then rule-based placement (gaps per difficulty, rest time per hand, alternating swings). On the built-in
  songs rendered to audio, 90% of detected onsets land within 8 ms of the real notes for three songs and within
  about 28 ms for Für Elise (still inside the PERFECT window); tempo within 0.1%.
- **Play** (`cantus.py`): fingertip swing detection (speed in palm widths per second, segment crossing the block,
  direction within 60 degrees), PERFECT / GOOD / OK windows, Beat Saber style multiplier, energy, best scores.
  Audio through `afplay` on the Mac (the same reason as the voice: no PortAudio in JAMES's process).
- **Review round** (independent agent, probes): fast swings at low frame rates read as zero speed; a partner hand
  dropping out for one frame made a phantom swing; quitting JAMES left the music (or a paused player) running; no
  hand way to leave; resuming paused again at once; stale cached audio after a melody change; `data/cantus/` not
  git-ignored; one failing converter stopped decoding; sounddevice ignored pause; two songs unreachable at
  640x480. All fixed with tests, plus: maps computed in chunks (a 6-minute track no longer needs 800 MB), silent
  files refused, a rescan retries failed files, swing speed scales with window width, hands timestamped at capture.
- Tests: `tests/test_cantus.py` (50). Live on the Mac: not yet; the audio offset needs one tuning pass there.

---

## Still open

1. The VOICE switch, website-in-browser handling and the first-word latency, live on the Mac.
2. Owner key design: the logs show the key is often switched off at the start of a session. Either let a single
   hand drive when only one person is in view, or make re-acquiring the key faster. Not decided.
3. Durable resume after a restart (a restart cancels the job today).
4. Signed industry packs (ed25519).
5. Chrome selectors, media keys, Instagram and WhatsApp prefill: UNVERIFIED.
6. Licence review of the neural voice's `phonemizer` / `espeak-ng` (GPL-3.0) before any commercial distribution.
