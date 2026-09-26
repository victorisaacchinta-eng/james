"""Lab paths and settings. Environment variables override the defaults."""
from __future__ import annotations

import os
from pathlib import Path

LAB = Path(__file__).resolve().parent
ROOT = LAB.parent                                   # the james-app folder (for james_core and packs/)
DEMO = LAB / "demo_data"
SENSORS = DEMO / "sensors"
DOCUMENTS = DEMO / "documents"
METADATA = DEMO / "metadata"
HISTORY_SEED = DEMO / "history_seed.yaml"
STATIC = LAB / "static"
PACKS = ROOT / "packs"

DB_PATH = Path(os.environ.get("LAB_DB", LAB / "data" / "lab.db"))
MODE = os.environ.get("LAB_MODE", "fixture")        # fixture | local_ai
OLLAMA_URL = os.environ.get("LAB_OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("LAB_OLLAMA_MODEL", "qwen3-vl:4b")
OLLAMA_TIMEOUT_S = float(os.environ.get("LAB_OLLAMA_TIMEOUT_S", "25"))
HOST = os.environ.get("LAB_HOST", "127.0.0.1")
PORT = int(os.environ.get("LAB_PORT", "8765"))

FIXTURE_LABEL = "Fixture mode: scripted guidance; AI is not active"
SYNTHETIC_LABEL = "Synthetic data. Not from a real machine."
ROLES_LABEL = "Demo roles, not authenticated"
