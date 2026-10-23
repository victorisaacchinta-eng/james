"""JAMES app settings. Change these, not the code."""
import os
from pathlib import Path

ROOT = Path(__file__).parent
ASSETS = ROOT / "assets"
DATA = ROOT / "data"
RECORDS = ROOT / "records"

# Local models (Ollama must be running: `ollama serve`)
OLLAMA_URL = "http://localhost:11434"
CHAT_MODEL = "qwen3-vl:4b"       # already on this Mac; fast on the M4
EMBED_MODEL = "nomic-embed-text"
LLM_TIMEOUT_S = 25

# Speech to text (downloaded once by setup.sh)
WHISPER_MODEL = "base.en"        # "small.en" hears names better but is ~3x slower on CPU
WHISPER_BEAM = 5                 # beam search width (1 = fastest, 5 = more accurate)

# Cloud fallback: OFF for the demo build. See james_core/egress.py before changing.
CLOUD_ENABLED = False

# Camera
CAMERA_INDEX = 0
MIRROR = True                    # show the camera like a mirror (detection still runs on the raw frame)
FRAME_W, FRAME_H = 1280, 720
HAND_MODEL = ASSETS / "hand_landmarker.task"
MAX_HANDS = 4                    # track up to this many hands; only one (the owner's) drives input
OWNER_RING = DATA / "owner_ring.json"   # the enrolled ring (press K in james.py); delete it to switch off
# Who may drive input: "key" = the hand wearing the printed owner key (ArUco id OWNER_KEY_ID, print it with
# `python tools/make_owner_key.py`), "ring" = the enrolled ring (K), "off" = the largest hand in view.
OWNER_MODE = "key"
OWNER_KEY_ID = 3                 # DICT_4X4_50; the owner's printed card (the old P-3 pump tag). Reserved: no pack may use it

# Industry pack: everything about the machines (rules, faults, sensors, manual, asset tags)
# comes from here. Spec: PACK_FORMAT.md. Pick another pack with JAMES_PACK=<pack-id>.
from james_core.pack import active_pack  # noqa: E402
PACK = active_pack()                     # refuses to start if any pack file was altered
PRIMARY_CLASS = PACK.primary_class

# Asset tags: ArUco DICT_4X4_50 id -> machine (from the pack)
ASSET_TAGS = PACK.asset_tags()
if OWNER_KEY_ID in ASSET_TAGS:
    raise SystemExit(f"Pack {PACK.manifest.id} uses ArUco id {OWNER_KEY_ID} as an asset tag, but that id is the "
                     f"owner key. Change OWNER_KEY_ID in config.py or the pack's aruco_id.")
POINT_STABLE_MS = 530            # the finger must stay on a tag this long before it counts (time, not frames:
                                 # 8 frames was 530 ms at 15 FPS but 800 ms at 10 FPS)
PINCH_RATIO = 0.33               # thumb-index gap / palm size below this = pinch (tune with gloves)
PINCH_FIST_LATCH = True          # a pinch that begins while the hand is a fist is ignored until the fingers open
PINCH_REACH_TOL = 0.3            # a held pinch whose hand size changes more than this (30%) is a reach, not a pinch
# SIGHT (james_core/sight.py): tracking-quality hints and the amber X when the hand is lost. SIGHT_LOW_LIGHT is
# mean frame brightness 0..255.
SIGHT = True
SIGHT_LOW_LIGHT = 55
# ARM TIME (james_core/armtime.py): suggest voice after this much weighted raised-arm time. 0 turns it off.
ARM_NUDGE_S = 45
# Motion (james_core/motion.py). 1-euro smoothing of the hand landmarks: lower SMOOTH_MIN_CUTOFF = steadier
# when still; higher SMOOTH_BETA = less lag on fast moves. A pinch selects where the finger aimed this long
# before the pinch began (closing the fingers drags the tip off target).
SMOOTHING = True
SMOOTH_MIN_CUTOFF = 1.0
SMOOTH_BETA = 0.01
PINCH_AIM_LOOKBACK_MS = 120
# ATLAS exploded view (X in app.py). REDUCED_MOTION: no animation, every change is instant.
REDUCED_MOTION = False
ATLAS_IDLE_SWAY = True           # a slow +/-9 degree sway when no hand is in view, so the 3D reads; off = still
# ATLAS hands (atlas_gestures.py, james_core/handpose.py). Openness per finger = tip-to-wrist / knuckle-to-wrist.
ATLAS_FIST_RATIO = 1.05          # a finger at or below this counts as fully closed
ATLAS_OPEN_RATIO = 1.75          # ... at or above this, fully open
ATLAS_EXPLODE_FROM = 0.12        # hand openness where the explode starts (below: assembled)
ATLAS_EXPLODE_TO = 0.85          # ... and where it is complete
ATLAS_TURN_DEG_PER_PX = 0.28     # moving an open hand: degrees of turn per pixel
ATLAS_TWIST_GAIN = 1.0           # twisting the hand: degrees of roll per degree of twist
ATLAS_START_PX = 6.0             # a resting hand must move this far before it turns anything (jitter guard)
ATLAS_START_DEG = 4.0            # ... or twist this far before it rolls anything
ATLAS_PEACE_HOLD_S = 0.5         # hold the V sign this long for the next model
ATLAS_MAX_FPS = 30               # the ATLAS app never draws faster than this (the camera gives about 30 anyway)
ATLAS_IDLE_AFTER_S = 3.0         # no hand for this long: look for one on every 3rd frame only (less heat)
ATLAS_FRAME_BUDGET_MS = 14.0     # if a full drawing takes longer, draw fewer lines while the view moves
ATLAS_TWO_ENTER_S = 0.2          # the second hand must be in view this long before two-hand zoom starts
# CANTUS rhythm game (cantus.py). Your songs go in data/cantus/songs (never committed).
CANTUS_DIFFICULTY = "normal"     # easy | normal | hard (D changes it in the game)
CANTUS_AUDIO_OFFSET_MS = 80      # music vs blocks; [ and ] adjust it live. Raise it if blocks arrive before the beat
CANTUS_SWING_PX_S = 420          # a swing is at least this fast (at 1280 px wide)...
CANTUS_SWING_PALMS_S = 4.0       # ...and at least this many palm widths a second
CANTUS_PAUSE_HOLD_S = 0.8        # hold the peace sign this long to pause
CANTUS_NO_FAIL = True            # True: running out of energy never ends the song

# Sample data (clearly labelled as sample everywhere it shows)
MANUAL = PACK.manual_path()             # packs/<pack>/docs/...
SEEDED_JOBS = ASSETS / "seeded_jobs.yaml"
DB = DATA / "james.db"
MEMORY_MAX_AGE_DAYS = 730                # older approved jobs are not offered as similar cases
RUNS_CSV = DATA / "runs.csv"

# Talkback: JAMES speaks, offline. TALK_ENGINE "auto" = the neural voice (Kokoro, assets/voice/) when installed,
# else macOS `say`. The HUD switch, M, "be quiet" / "talk to me" turn it off/on; the switch is remembered in
# data/prefs.json together with the voice picked by `python -m talk --use NAME`. TALK_VOICE "" = the default
# (neural: bm_fable; say: the best male English voice installed). TALK_SIR is how JAMES addresses you.
TALKBACK = True
TALK_ENGINE = "auto"
# opening ChatGPT/Claude with a prompt only FILLS the box. JAMES presses send only when you say
# 'send it', and only in the tab holding that exact prompt. The page selectors are UNVERIFIED, so this stays False.
ASK_AUTO_SEND = False
TALK_VOICE = ""
TALK_RATE = 185
TALK_SIR = "sir"

# Launcher: Gmail, Drive and Google Calendar links open this account directly (authuser=)
GOOGLE_ACCOUNT = os.environ.get("JAMES_GOOGLE_ACCOUNT") or None   # set it in your shell; not stored in the repo
