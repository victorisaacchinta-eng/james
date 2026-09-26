"""Run the JAMES Workflow Lab:  python -m lab   then open http://127.0.0.1:8765

    python -m lab --reset      start with an empty ledger (the old one is kept as lab.db.bak-<time>)
    LAB_MODE=local_ai python -m lab    optional: guidance from a local Ollama model (validated)
"""
import argparse
import time
from pathlib import Path

from . import settings


def main():
    ap = argparse.ArgumentParser(prog="python -m lab")
    ap.add_argument("--reset", action="store_true", help="move the current ledger aside and start empty")
    ap.add_argument("--port", type=int, default=settings.PORT)
    a = ap.parse_args()
    db = Path(settings.DB_PATH)
    if a.reset and db.exists():
        db.rename(db.with_name(f"{db.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}"))
        print(f"Old ledger kept as {db.parent.name}/{db.name}.bak-*")
    import uvicorn
    from .api import create_app
    app = create_app()
    lab = app.state.lab
    print(f"JAMES Workflow Lab | {settings.SYNTHETIC_LABEL}")
    print(f"  mode    : {lab.developer()['mode_label']}")
    for pid, p in lab.packs.items():
        print(f"  pack    : {p.hud_label}")
    print(f"  ledger  : {db}")
    print(f"  open    : http://{settings.HOST}:{a.port}")
    uvicorn.run(app, host=settings.HOST, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
