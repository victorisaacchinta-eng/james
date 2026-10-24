"""A stable identifier for exactly this build (iteration 2, section 9).

    python -m tools.build_id          # prints: JAMES build <12 hex> (<n> files)

sha256 over every source file that decides behaviour: *.py (tests included), packs/, assets/*.yaml,
lab/static and lab/data fixtures, sorted by path, each as '<path>\\0<sha256>\\n'. Logs, records, the venv,
backups and caches are left out, so running JAMES does not change the id; editing any code or pack does."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = {".venv", "_backups", "_to_delete", "__pycache__", ".pytest_cache", "records", "_private", "test-run-records"}
SUFFIX = {".py", ".yaml", ".yml", ".csv", ".pdf", ".md", ".html", ".js", ".css", ".json", ".txt", ".sh"}


def files(root: Path = ROOT) -> list[Path]:
    out = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if any(part in SKIP or part.startswith(".") or part.endswith("outputs") for part in rel.parts) or not p.is_file():
            continue
        if rel.parts[0] == "data" or p.suffix not in SUFFIX:
            continue                                  # data/: logs, ledgers, contacts: runtime, not build
        if rel.parts[0] == "assets" and p.suffix not in (".yaml", ".yml"):
            continue                                  # icons and fonts: generated or binary, not behaviour
        if p.suffix == ".md" and rel.parts[0] not in ("packs", "lab"):
            continue                                  # docs change without changing the build
        out.append(p)
    return out


def build_id(root: Path = ROOT) -> tuple[str, int]:
    h = hashlib.sha256()
    fs = files(root)
    for p in fs:
        h.update(f"{p.relative_to(root).as_posix()}\0{hashlib.sha256(p.read_bytes()).hexdigest()}\n".encode())
    return h.hexdigest()[:12], len(fs)


PACKAGES = ("numpy", "opencv-contrib-python", "mediapipe", "faster-whisper", "onnxruntime", "kokoro-onnx",
            "pydantic", "langgraph", "qdrant-client", "pymupdf", "pandas", "sounddevice")


def environment() -> dict:
    """What the build ran on (iteration 3, section 7): Python, OS, package versions, the local model and the pack."""
    import platform
    from importlib import metadata
    sys.path.insert(0, str(ROOT))
    env = {"python": platform.python_version(), "os": f"{platform.system()} {platform.release()} {platform.machine()}"}
    for pkg in PACKAGES:
        try:
            env[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            env[pkg] = "not installed"
    try:
        import config
        from agents import llm
        env["pack"] = f"{config.PACK.label} snapshot {config.PACK.snapshot}"
        env["model"] = llm.model_info()
        import talk
        env["voice_files"] = "ok" if talk.neural_files_ok() else "missing"
    except Exception as e:  # noqa: BLE001
        env["error"] = f"{type(e).__name__}: {e}"
    return env


if __name__ == "__main__":
    bid, n = build_id()
    print(f"JAMES build {bid} ({n} files)")
    if "--env" in sys.argv:
        for k, v in environment().items():
            print(f"  {k}: {v}")
