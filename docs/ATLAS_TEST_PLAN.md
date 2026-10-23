# ATLAS test plan (run on the Mac)

A manual checklist for the Mac. Each step says what to type and what should happen. Tick the box, or write
what actually happened. Take a screenshot at the steps marked (screenshot).

## 0. Install the build

1. Get the build into a folder called `james`.
2. In Terminal:
   ```bash
   cd ~/Downloads/james
   bash setup.sh
   ```
   After a few minutes it should print `PACK CHECK: PASS` and `RESULT: PASS`. The test line says
   `579 passed, 1 skipped` (the skip needs the neural voice files; after `python -m tools.get_voice` it becomes
   `580 passed`).
   - [ ] PASS

3. Activate the environment for every step below:
   ```bash
   source .venv/bin/activate
   ```

## 1. The library, without a camera

1. `python atlas_app.py --list`
   7 lines: perseverance (13 parts), ingenuity (9), jwst (10), car_concept (15), chronograph (11),
   flight_helmet (6), and the pump (14). No line starts with `LEFT OUT`.
   - [ ] 7 models
2. `python atlas_app.py --demo`
   8 file paths in `data/atlas_frames/`. Open the folder (`open data/atlas_frames`): each picture shows
   one exploded model with its first part in cyan and a card on the left. The last picture is the rover zoomed in.
   Screenshot one picture.
   - [ ] 8 pictures look right

## 2. The ATLAS app with the camera

`python atlas_app.py` (macOS asks for camera access the first time: allow it for Terminal, then run again).

Stand 40 to 70 cm from the camera, palm facing it. ATLAS follows `OWNER_MODE` in `config.py` like james.py: in
key mode only the hand wearing the owner key drives it. No key at hand? Press **X** once: any hand drives
(the largest in view), and the second hand is used for two-hand zoom.

Watch the **legend** on the left while you test: the row ATLAS thinks you are doing is lit amber. If the lit row
is not what you are doing, write down which one it showed. That is the most useful note for tuning.

| # | Do | Expect | OK? |
|---|---|---|---|
| 1 | Nothing | The rover is exploded; with no hand in view it sways slowly. TRACKING says "No hand" | [ ] |
| 2 | Show an open hand, hold it still for 3 seconds | "Open slowly" lit; a ring around your hand; the rover does NOT turn | [ ] |
| 3 | Close your hand slowly into a fist | The rover assembles as you close; "Close your hand" lit; EXPLODE goes to 0% (screenshot) | [ ] |
| 4 | Open it slowly again | It explodes as you open; stop half way and it stays half exploded | [ ] |
| 5 | Open hand: move it left and right, up and down | It turns and tilts with your hand; "Move or twist" lit | [ ] |
| 6 | Open hand: twist your wrist like turning a doorknob | It rolls the same way | [ ] |
| 7 | Point with your index finger only, at a wheel | Wheels light up; the sidebar card says "Pointing at Wheels"; the explode does NOT drop (screenshot) | [ ] |
| 8 | From pointing, pinch (thumb to index) and drag | The wheels come out and follow your pinch; "Pinch a part" lit (screenshot) | [ ] |
| 9 | Let go | The wheels spring back into place and stay picked (cyan) | [ ] |
| 10 | Pinch on empty space and let go | The pick clears; nothing turns | [ ] |
| 11 | Make a fist: does anything get picked or pulled? | No. A fist is never a pinch | [ ] |
| 12 | Bring your second hand in, then move hands apart and together | After a moment "Two hands" lit, a dashed line and ZOOM between them; it zooms (screenshot) | [ ] |
| 13 | Take the second hand away | The zoom stays; one-hand control comes back after a moment | [ ] |
| 14 | Peace sign (V), hold it | A cyan arc fills on the ring; after half a second the next model loads, only once | [ ] |
| 15 | Quick pinch on "Concept car" in the shelf | The car loads, exploded | [ ] |
| 16 | Try every tile: Ingenuity, Webb, watch, helmet, pump | Each loads; credit line at top right | [ ] |
| 17 | Quick pinch on Reset view | Back to the start angle, zoom 1x | [ ] |
| 18 | Keys: A, D, W, S, +, -, E, N, B, Tab, R | Turn, tilt, zoom, explode, next/previous model, next part, reset | [ ] |
| 19 | Quick pinch on Close (or Esc) | The window closes | [ ] |
| 20 | Look at the colours and the FPS top right | Wheels saffron, body steel blue, and so on, with a key bottom right; FPS near 30 | [ ] |
| 21 | Take your hand away for 5 seconds, then bring it back | It finds your hand again within a blink (tracking slows down while no hand is in view, to save heat) | [ ] |

Write down any gesture that misfires and how (too sensitive, too slow, wrong row lit). The numbers to tune are
all in `config.py` under `ATLAS_*`: `ATLAS_FIST_RATIO` / `ATLAS_OPEN_RATIO` (when the hand counts as closed or
open), `ATLAS_TURN_DEG_PER_PX` (turn speed), `ATLAS_START_PX` (how far a resting hand must move before it turns
anything), `ATLAS_PEACE_HOLD_S`, `ATLAS_TWO_ENTER_S`.

## 2b. CANTUS, the rhythm game

`python cantus.py` (or, inside `python james.py`, the CANTUS tile). Turn the volume up. Stand back so both hands
fit in the frame with room to swing.

| # | Do | Expect | OK? |
|---|---|---|---|
| 1 | Nothing | Four song cards (Ode to Joy, Für Elise, Canon in D, Turkish March), Easy / Normal / Hard, Close | [ ] |
| 2 | Point at Hard and pinch | Hard is lit; the cards' block counts go up | [ ] |
| 3 | Pinch Normal, then pinch Ode to Joy | 3-2-1, then the music starts and blocks fly in (screenshot) | [ ] |
| 4 | Swing through a block the way its arrow points, as it reaches the frame | It splits; PERFECT or GOOD; the score and combo go up | [ ] |
| 5 | Swing the wrong way through one | BAD; combo back to 0 | [ ] |
| 6 | Let one pass | MISS; the multiplier drops a step | [ ] |
| 7 | Are blocks reaching the frame on the beat? | If they're early press ] a few times, if late press [ (10 ms each). Note the final number shown | [ ] |
| 8 | Peace sign, hold it | Pauses and the music stops; pinch Resume and it carries on from the same spot | [ ] |
| 9 | Play to the end | Results: rank, score, accuracy, counts; pinch Play again or Songs (screenshot) | [ ] |
| 10 | Copy an MP3 into `data/cantus/songs/`, press R | "Mapping the beat...", then a blue YOUR FILE card; play it | [ ] |
| 11 | Pinch Close (or Esc) | Back to JAMES (or the window closes) | [ ] |

Note the audio offset that felt right (row 7) and any swing that should have counted but didn't.

## 3. ATLAS inside the everyday assistant

`python james.py`

| # | Do | Expect | OK? |
|---|---|---|---|
| 1 | Open your palm and hold it | The launcher opens; ATLAS is one of the tiles (dark tile, cyan cube) (screenshot) | [ ] |
| 2 | Point at ATLAS and pinch-hold | "Opening ATLAS", then ATLAS fills the window | [ ] |
| 3 | Open your palm inside ATLAS | ATLAS explodes the model; the launcher does NOT open | [ ] |
| 4 | Show the V sign inside ATLAS and hold it | The next model loads; voice does NOT start (voice gestures are off in ATLAS) | [ ] |
| 5 | Esc | Back to JAMES; an open palm right away does not reopen the launcher straight away | [ ] |
| 6 | V, say "open atlas", fist | ATLAS opens | [ ] |
| 7 | U | ATLAS closes and JAMES locks; U again unlocks | [ ] |
| 8 | `cat data/james_demo_log.txt \| grep ATLAS` | Lines for open and close | [ ] |

## 4. The maintenance app still works

`python app.py --sim` then `open data/sim_frames`

12 frames; `06_atlas_blocked.png` shows the pump with the coupling guard in red.

Then `python app.py` live: U, point at the P-3 tag, pinch (PPE), 1 (scripted request), S, S.

| # | Do | Expect | OK? |
|---|---|---|---|
| 1 | X | ATLAS opens beside the card; coupling guard red | [ ] |
| 2 | Point at the guard, then pinch it | Card: "This step is blocked by LOTO-01, ZERO-01"; the strip at the bottom lights the gesture | [ ] |
| 3 | Pinch and hold for 2 seconds anywhere | Nothing is confirmed (the task card does not change) | [ ] |
| 4 | Both hands pinching, spread | The pump zooms | [ ] |
| 5 | Press C or L | "ATLAS is open: close it (X) first" | [ ] |
| 6 | X, then pinch-hold | Now the pinch confirms lockout as before | [ ] |

## 5. Keep

The ticked plan, the screenshots, and `data/james_demo_log.txt`. For anything that failed: which step, and what you saw.
