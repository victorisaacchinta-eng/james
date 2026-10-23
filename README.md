<p align="center">
  <img src="assets/readme/hero.svg" alt="JAMES: Joint Autonomous Multimodal Execution System. Point. Ask. Pinch to confirm." width="100%">
</p>

<p align="center">
  <img alt="macOS" src="https://img.shields.io/badge/runs%20on-macOS-35E0D0?style=flat-square&labelColor=0D1417&logo=apple&logoColor=EAF4F4">
  <img alt="Python 3.11" src="https://img.shields.io/badge/python-3.11-35E0D0?style=flat-square&labelColor=0D1417&logo=python&logoColor=EAF4F4">
  <img alt="Local model" src="https://img.shields.io/badge/model-qwen3--vl%3A4b%20via%20Ollama-35E0D0?style=flat-square&labelColor=0D1417">
  <img alt="Cloud off" src="https://img.shields.io/badge/cloud-off-4CC38A?style=flat-square&labelColor=0D1417">
  <img alt="607 tests passing" src="https://img.shields.io/badge/tests-607%20passing-4CC38A?style=flat-square&labelColor=0D1417">
  <img alt="MIT licence" src="https://img.shields.io/badge/licence-MIT-F2B544?style=flat-square&labelColor=0D1417">
  <a href="https://jamesagent.vercel.app/"><img alt="Website" src="https://img.shields.io/badge/website-jamesagent.vercel.app-35E0D0?style=flat-square&labelColor=0D1417"></a>
</p>

<h3 align="center">A touch-free, multi-agent maintenance copilot.<br>Point at the machine. Ask out loud. Pinch to confirm each step.</h3>

<p align="center"><a href="https://jamesagent.vercel.app/"><b>jamesagent.vercel.app</b></a> &nbsp;·&nbsp; the product site</p>

<p align="center">
  <a href="#-see-it">See it</a> ·
  <a href="#-how-it-works">How it works</a> ·
  <a href="#-the-safety-gate">Safety gate</a> ·
  <a href="#-quick-start">Quick start</a> ·
  <a href="#-the-90-second-demo">Demo</a> ·
  <a href="#-agents">Agents</a> ·
  <a href="https://jamesagent.vercel.app/">Website</a>
</p>

<img src="assets/readme/divider.svg" width="100%" alt="">

A technician with gloves on and both hands busy cannot scroll a PDF manual. JAMES watches the camera, listens,
finds the right procedure in the manual, checks the machine's sensor log and past repairs, and walks through the
fix one step at a time. Every step needs a physical pinch to confirm, and a rule engine with no AI in it refuses
any step that is unsafe. Everything runs on the laptop: local model, local search, local memory.

> [!NOTE]
> This is a demo build. It ships with **sample data** and **demo safety rules** (PUMP-UTIL v0.1). They are not
> approved for any real plant, and nothing here is certified for safety use.

## ◆ See it

<p align="center">
  <img src="assets/readme/walkthrough.gif" width="100%" alt="The JAMES screen walking through a pump repair: locked, target the pump, step 1 and 2 with sources, step 4 blocked by the safety policy, lockout and zero energy confirmed, job complete, and the second run recalling the approved job.">
  <br>
  <sub>The real app screens from rehearsal mode (<code>python app.py --sim</code>): lock, target the pump, sourced steps, a safety block, the fix, and a second run that recalls the approved job.</sub>
</p>

## ◆ How it works

<p align="center">
  <img src="assets/readme/flow.svg" width="100%" alt="Pipeline: LENS sees, ECHO hears, FOREMAN plans with PAGE, PULSE and LEDGER, WARDEN checks safety rules, the pinch confirms each step.">
</p>

<table>
<tr>
<td width="33%" valign="top">

**Hands-free by design**<br>
<sub>Point to pick a target, V sign to talk, pinch and hold to confirm. Keyboard fallbacks are labelled on screen and in the log.</sub>

</td>
<td width="33%" valign="top">

**Every answer has a source**<br>
<sub>Steps come from the manual with page citations. Readings come from the supplied log. Nothing is invented; if the evidence is weak, it says so.</sub>

</td>
<td width="33%" valign="top">

**It learns from approved jobs**<br>
<sub>When a senior approves a repair, the next similar request recalls it first. Unapproved jobs are never recalled.</sub>

</td>
</tr>
<tr>
<td valign="top">

**Status bar**<br>
<sub>The top bar always shows which backend is live (model or rules, BM25 or BM25 + Qdrant), so nothing is overstated.</sub>

</td>
<td valign="top">

**Industry packs, not hard-coding**<br>
<sub>Rules, faults, sensors, manuals and asset tags live in a pack. The app refuses to start if a pack file changed after its manifest.</sub>

</td>
<td valign="top">

**A voice that talks back**<br>
<sub>Optional neural voice (Kokoro-82M, on-device), with a switch in the header to mute it.</sub>

</td>
</tr>
</table>

## ◆ The safety gate

<p align="center">
  <img src="assets/readme/warden.svg" width="100%" alt="WARDEN blocks step 4, Remove the coupling guard, until lockout and zero energy are confirmed, then allows it.">
</p>

WARDEN is plain Python plus the pack's rule file. The model can suggest a step; only WARDEN can let it through,
and only a pinch on the current, unexpired, allowed step moves the job forward.

**The pinch has to be a pinch.** A pinch that starts as a closing fist, or whose hand moves toward or away from the
camera while held (reaching for a tool), never confirms a step. **SIGHT** says when tracking can't be trusted (low
light, a hand that keeps dropping out, often dark gloves) and marks a lost hand with a still amber X instead of
guessing. Every logged event is **hash-chained**: `python -m tools.verify_ledger` (read-only) names the first edited,
unlinked or removed event, and each repair record ends with the chain head, which `--expect SEQ:HASH` checks later.
Details, limits and sources: `docs/ITERATIONS.md`, iteration 10.

## ◆ ATLAS: where each step lands on the machine

<p align="center">
  <img src="assets/readme/atlas.png" width="100%" alt="ATLAS: an exploded line drawing of pump P-3 next to the task card. The coupling guard is red because WARDEN is blocking step 4 until lockout and zero energy are confirmed.">
</p>

Press **X** and JAMES draws the targeted machine as an exploded schematic in the free part of the camera view.
The part the current step touches is lit: **amber** when WARDEN allows the step, **red** when WARDEN is blocking it.
Pinch any part to see what it does and which rules guard it, as a WARDEN preview with the job's current facts.

- **No AI decides a colour.** A step lights a part through the same hazard tags WARDEN uses (`coupling_guard`,
  `bearing_housing`...) and the part's own words in the manual step. The preview calls `safety_guard.evaluate`.
- **The model ships in the pack** (`models/<class>.yaml`, format `james-model/0.1`), hash-checked with every other
  pack file. The demo pump is labelled on screen as a **schematic, not OEM CAD**.
- **A pinch in ATLAS can never confirm a step.** ATLAS owns the hand while it is open: opening your hand slowly
  explodes the machine and closing it assembles it, moving or twisting it turns the model, pointing lights a part,
  pinching a part pulls it out, two hands zoom. A legend lights the gesture it sees. Close it (X) to confirm.
- Every part picked is logged with the time since ATLAS opened (`atlas_part_selected`), which is how
  "time to find the part a step refers to" gets measured instead of claimed.

### The ATLAS app: real machines, in your hands

ATLAS is also an app of its own in the everyday assistant (`python james.py`): open your palm for the launcher and
pinch the **ATLAS** tile, or say "open atlas". Or run it alone with `python atlas_app.py`. A shelf at the bottom
holds seven models; pinch one to load it, then take it apart with the same gestures.

<p align="center">
  <img src="assets/readme/atlas_app.png" width="100%" alt="The ATLAS app: NASA's Perseverance rover as an exploded line drawing, the wheels picked in cyan with their description card, and a shelf of seven model thumbnails at the bottom.">
</p>

| Model | Parts | Source and licence |
|---|---|---|
| Mars 2020 Perseverance rover | 13 (wheels, rocker-bogie, arm, turret, mast, MMRTG, antennas, RIMFAX...) | NASA 3D Resources, public domain |
| Ingenuity Mars helicopter | 9 (solar panel, two rotors, fuselage, cameras, four legs) | NASA 3D Resources, public domain |
| James Webb Space Telescope | 10 (mirrors, sunshield, mid booms, bus, solar array, antenna...) | NASA 3D Resources, public domain |
| Concept car | 15 (four wheels, doors, hood, glass, roll cage, seats, cockpit...) | Khronos glTF Sample Assets, CC BY 4.0 |
| Chronograph watch | 11 (crystal, hands, dial, bezel, case, back, strap, buckle) | Khronos glTF Sample Assets, CC BY 4.0 |
| Flight helmet | 6 (lenses, frames, fittings, leather shell, mask, hose) | Khronos glTF Sample Assets, CC0 |
| Centrifugal pump | 14 | This repo's industry pack (schematic) |

- The models were converted once by `tools/atlas_import.py` into line drawings (feature edges plus a light wireframe
  of each part's shape) in `atlas_library/`. The app reads them with NumPy only and refuses any file whose hash
  does not match `atlas_library/library.yaml`.
- Every model carries its credit on screen. Full credits: [`atlas_library/NOTICE.md`](atlas_library/NOTICE.md).
  JAMES is not endorsed by NASA, Khronos or any model author; textures and logos are not used.
- `python atlas_app.py --demo` saves a picture of every model without a camera.

<img src="assets/readme/divider.svg" width="100%" alt="">


### CANTUS: slice to the beat

A rhythm game in the launcher (the **CANTUS** tile, or "open cantus"; alone: `python cantus.py`). Blocks fly
towards you and reach the frame on the beat; swing through each one the way its arrow points, red with your left
hand and blue with your right. Timing gives PERFECT, GOOD or OK; hits in a row raise the multiplier to x8.

<p align="center">
  <img src="assets/readme/cantus.png" width="100%" alt="CANTUS: a red block cut in two by a swing, PERFECT +230, a blue up-arrow block on its way down the lanes, score and x4 multiplier at the top.">
</p>

- **Built in:** Ode to Joy, Für Elise, Canon in D and Turkish March, all public domain, written out as plain
  melodies and synthesised in `james_core/cantus_songs.py`. No recording or anyone's arrangement is copied.
- **Your own songs:** drop an MP3, M4A or WAV into `data/cantus/songs/` and press R. JAMES finds the tempo and the
  onsets (`james_core/beatmap.py`: spectral flux, autocorrelation, no model) and builds easy, normal and hard maps
  once. Your files never leave your Mac and `data/cantus/` is git-ignored.
- Same chart every time, playable by design: blocks at least 0.4 to 0.95 s apart, each hand given time between
  its own blocks, swings alternating down and up.

## ◆ Quick start

```bash
git clone https://github.com/victorisaacchinta-eng/james.git
cd james
bash setup.sh
```

`setup.sh` makes a virtual environment, installs everything, downloads the speech model and runs the tests. It ends
with `580 passed` (the `tests/` folder), `PACK CHECK: PASS` and `RESULT: PASS`. The full suite,
`python -m pytest -q`, also runs `lab/tests` (607 passed on macOS with the neural voice files installed, Python 3.11).

If [Ollama](https://ollama.com) is installed, setup also pulls `qwen3-vl:4b` and `nomic-embed-text`. Without Ollama,
JAMES still runs with keyword rules and BM25 search, and says so on screen.

<details>
<summary><b>Neural voice (optional)</b></summary>

<br>

`setup.sh` installs `kokoro-onnx==0.6.1` but not the voice model files (348 MB, too big for GitHub). Fetch them once:

```bash
source .venv/bin/activate
python -m tools.get_voice
```

It downloads `kokoro-v1.0.onnx` (326 MB) and `voices-v1.0.bin` (28 MB) from the
[kokoro-onnx release `model-files-v1.1`](https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.1)
into `assets/voice/`, checks each against a pinned sha256 and refuses a mismatch. It ends with `neural voice: ready`.
Without these files JAMES speaks with the macOS `say` voice.

</details>

<details>
<summary><b>Optional settings</b></summary>

<br>

- `export JAMES_GOOGLE_ACCOUNT=you@example.com` makes the Gmail, Drive and Calendar launcher links open that account.
- `config.py`: `PINCH_RATIO` (raise it if gloves make pinching hard), `POINT_STABLE_FRAMES`, `CAMERA_INDEX`, `WHISPER_MODEL`.
- `JAMES_PACK=<pack-id>` picks a different industry pack (default `pharma-utility`).

</details>

### Run

```bash
source .venv/bin/activate
python app.py              # camera + microphone
python app.py --no-voice   # camera only; key 1 sends the request
python app.py --sim        # rehearsal: no camera or mic, saves every screen to data/sim_frames/
python james.py            # the everyday assistant: gestures, voice, app launcher (with the ATLAS app)
python atlas_app.py        # ATLAS on its own: the model library with hand controls
python cantus.py           # CANTUS on its own: the rhythm game
```

macOS asks for Camera and Microphone access for Terminal the first time: allow both, then run again.
Click the JAMES window once so it receives key presses.

Before a live demo: print `assets/asset_tag_P-3.pdf` at 100% and stick it on the "pump" (or show
`asset_tag_P-3.png` full screen on a phone), and start the local model with `ollama serve`.

## ◆ The 90-second demo

| Do | JAMES shows |
|---|---|
| **U** | Unlocks. "Point at the machine's asset tag." |
| Point at the tag | Ring on P-3 with confidence. (Tag visible but not pointed at: "Is this P-3?", pinch to confirm) |
| **Pinch** (hold) | PPE confirmed |
| **V**, say *"Pump 3 is vibrating more than usual"*, **V** | Transcript, then PAGE + PULSE + LEDGER in parallel, likely cause, Step 1 with sources |
| **Pinch** | Step 1 confirmed and logged, Step 2 appears |
| **S**, **S** (skip lockout on purpose) | Step 4 "Remove the coupling guard": **BLOCKED BY SAFETY POLICY, LOTO-01, ZERO-01** |
| **X**, pinch the red part, **X** | ATLAS: the pump explodes, the coupling guard is red; its card says which rules gate it and what they need |
| **Pinch**, **Pinch** | Lockout, then zero energy confirmed; WARDEN re-checks; step allowed |
| **Pinch**, **Pinch** | Steps 4 and 5 confirmed; job complete; repair record saved in `records/` |
| **A**, then **R** and repeat the request | A senior approves the job; the second run recalls it first ("Similar case: job:J-1xx") |

Backup if the mic fails: key **1** sends the scripted request, labelled **[SCRIPTED INPUT]** on screen.
Backup if the pinch fails with gloves: key **C** confirms, labelled and logged as `key_fallback`.

<details>
<summary><b>Everyday assistant (<code>python james.py</code>): gestures and voice</b></summary>

<br>

| Do | What happens |
|---|---|
| Open palm (idle) | Launcher: point at an app and pinch to open it |
| **V sign** (hold ~0.4 s) | Starts listening. Keep holding while you talk |
| **Fist** | Stops listening and runs the command. Open palm cancels |
| Say "open Chrome and play Despacito" / "play believer on Spotify" | The voice window shows what was heard and each step, with a tick or a cross |
| **K** / **X** | Enrol / forget the owner ring (a visual filter that keeps background hands out, not security; **U** locks) |

Voice commands open apps and web pages, search, play music and collect links into Notes. JAMES never sends a
message to a person by itself, never handles logins and never presses send in an AI chat.

</details>

## ◆ Agents

| Agent | Job | Where | Stack |
|---|---|---|---|
| **LENS** | Sees hands and the asset tag | `agents/lens.py` | MediaPipe Hand Landmarker, OpenCV ArUco |
| **ECHO** | Hears the request | `agents/echo.py` | sounddevice, faster-whisper `base.en`, Qwen3-VL 4B via Ollama (keyword rules if offline) |
| **PAGE** | Finds the procedure | `agents/page.py` | PyMuPDF, BM25, nomic-embed-text in Qdrant (in-process); re-queries when weak; cites pages |
| **PULSE** | Reads the sensor log | `agents/pulse.py` | pandas on the supplied log; never invents a reading |
| **LEDGER** | Remembers approved jobs | `agents/recall.py`, `james_core/ledger.py` | SQLite, append-only |
| **FOREMAN** | Plans and decides the cause | `agents/foreman.py` | LangGraph fan-out to PAGE, PULSE, LEDGER; local model or source vote |
| **WARDEN** | Blocks unsafe steps | `james_core/safety_guard.py` | Plain Python + the pack's rules; no AI |
| **Pinch gate** | Confirms each step | `app.py`, `james_core/session.py` | Pinch and hold 600 ms on the current, unexpired, allowed step |
| **ATLAS** | Shows where the step is on the machine | `atlas.py`, `james_core/model3d.py` | NumPy + OpenCV line drawing of the pack model; no AI |
| **Motion** | Steadies the hand, fixes pinch drift, reads swipes | `james_core/motion.py`, `agents/lens.py` | 1-euro filter, pre-pinch aim point, swipe detector |
| **Hand poses** | Open, fist, point, pinch, peace; how open the hand is | `james_core/handpose.py`, `atlas_gestures.py` | Geometry ratios with hysteresis and a confirm time; a fist is never a pinch |
| **ATLAS app** | Model library with hand controls | `atlas_app.py`, `james_core/atlas_library.py`, `atlas_library/` | NumPy line drawings of openly licensed models; no AI |
| **CANTUS** | Rhythm game: slice blocks to the beat | `cantus.py`, `james_core/beatmap.py`, `james_core/cantus_songs.py` | NumPy onset and tempo detection, a small synth; no AI |

## ◆ Industry packs

Everything about the machines lives in `packs/<pack-id>/`, not in the code: WARDEN rules and their seeded cases,
the fault vocabulary, sensor channels, the manual and the asset tags. The format is in [`PACK_FORMAT.md`](PACK_FORMAT.md).

```bash
python -m james_core.pack check packs/pharma-utility    # validate + run the seeded cases
python -m james_core.pack rehash packs/pharma-utility   # after you edit a pack file (dev only)
```

Packs are not signed yet.

## ◆ Numbers, and where they come from

- `python -m james_core.suite` prints the seeded safety result: **8/8 violations blocked, 0/4 false blocks**.
- Every live run appends to `data/runs.csv` (`--sim` runs and tests do not): time from the end of speech to the
  first sourced step, and which backends were live. Timing claims should only be quoted from that file.
- Files it writes: `data/james.db` (log and jobs), `data/runs.csv`, `records/repair_record_*.md`,
  `records/escalation_*.md`. Delete `data/james.db` to reset the demo memory.

## ◆ Roadmap

- Signed packs (ed25519) and a second pack with an air-handling unit
- ATLAS: import OEM models (glTF) into packs, and pin the drawing to the real machine using the asset tag's pose
- Simulated Modbus / MQTT sensor adapter
- Windows, Linux and edge devices (today it runs on macOS)
- Telugu and Hindi voice

## ◆ More documents

- [`USER_MANUAL.md`](USER_MANUAL.md): every gesture, key and voice command
- [`PACK_FORMAT.md`](PACK_FORMAT.md): the industry pack format
- [`docs/ITERATIONS.md`](docs/ITERATIONS.md): how the build evolved, iteration by iteration, with what ran live on a Mac and what is only tested

## ◆ Licence

MIT, see [`LICENSE`](LICENSE). Third-party parts keep their own licences. The optional neural voice (`kokoro-onnx`)
pulls in `phonemizer` / `espeak-ng`, which are GPL-3.0; pip installs them on your machine and they are not included
in this repository. The Kokoro-82M model is Apache-2.0.

<img src="assets/readme/divider.svg" width="100%" alt="">

<p align="center"><sub>Built by <a href="https://github.com/victorisaacchinta-eng">Chintha Victor Isaac</a>.</sub></p>
