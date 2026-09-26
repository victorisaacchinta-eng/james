"""Check (and if missing, fetch) JAMES's neural voice files into assets/voice/.

    python -m tools.get_voice

Files: kokoro-v1.0.onnx (326 MB, full precision) and voices-v1.0.bin (28 MB) from the kokoro-onnx GitHub release
model-files-v1.1 (model: Kokoro-82M, Apache-2.0). Each file is checked against a pinned sha256; a file that does
not match is refused. Not the int8 model (its ConvInteger node has no CPU kernel on ARM in onnxruntime) and not fp16
(3 of 54 test sentences came out silent; full precision: 0 of 54, and no slower).
Also needed once: pip install kokoro-onnx==0.6.1"""
from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import talk as T  # noqa: E402


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main() -> bool:
    T.VOICE_DIR.mkdir(parents=True, exist_ok=True)
    ok = True
    for name, digest in T.MODEL_FILES.items():
        p = T.VOICE_DIR / name
        if not p.is_file():
            print(f"downloading {name} ...")
            tmp = p.with_suffix(p.suffix + ".part")
            urllib.request.urlretrieve(T.MODEL_URL + name, tmp)
            tmp.rename(p)
        got = sha256(p)
        good = got == digest
        ok &= good
        print(f"{name}: {'OK' if good else 'HASH MISMATCH (refused): ' + got}")
    avail, why = T.neural_available()
    print("neural voice:", "ready" if avail and ok else f"not ready ({why or 'bad file'})")
    return ok


if __name__ == "__main__":
    main()
