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

# Sample data (clearly labelled as sample everywhere it shows)
MANUAL = PACK.manual_path()             # packs/<pack>/docs/...
SEEDED_JOBS = ASSETS / "seeded_jobs.yaml"
DB = DATA / "james.db"
MEMORY_MAX_AGE_DAYS = 730                # iteration 2 P1-D: older approved jobs are not offered as similar cases
RUNS_CSV = DATA / "runs.csv"

# Talkback: JAMES speaks, offline. TALK_ENGINE "auto" = the neural voice (Kokoro, assets/voice/) when installed,
# else macOS `say`. The HUD switch, M, "be quiet" / "talk to me" turn it off/on; the switch is remembered in
# data/prefs.json together with the voice picked by `python -m talk --use NAME`. TALK_VOICE "" = the default
# (neural: bm_fable; say: the best male English voice installed). TALK_SIR is how JAMES addresses you.
TALKBACK = True
TALK_ENGINE = "auto"
# Iteration 2 P0-C: opening ChatGPT/Claude with a prompt only FILLS the box. JAMES presses send only when you say
# 'send it', and only in the tab holding that exact prompt. The page selectors are UNVERIFIED, so this stays False.
ASK_AUTO_SEND = False
TALK_VOICE = ""
TALK_RATE = 185
TALK_SIR = "sir"

# Launcher: Gmail, Drive and Google Calendar links open this account directly (authuser=)
GOOGLE_ACCOUNT = os.environ.get("JAMES_GOOGLE_ACCOUNT") or None   # set it in your shell; not stored in the repo
