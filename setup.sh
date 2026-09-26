#!/bin/bash
# One-time setup on macOS. Run from this folder:  bash setup.sh
set -e
cd "$(dirname "$0")"
PY=""
for v in python3.12 python3.11 python3.13 python3; do
  if command -v $v >/dev/null 2>&1; then PY=$v; break; fi
done
echo "==> Using $($PY --version)"
$PY -m venv .venv
source .venv/bin/activate
pip install --upgrade pip -q
echo "==> Installing libraries (a few minutes the first time)"
pip install -r requirements.txt -q
python -c "import mediapipe, cv2, faster_whisper, pymupdf, pandas, langgraph, qdrant_client; print('==> Libraries OK')"
echo "==> Downloading the speech model (about 150 MB, once)"
python -c "from faster_whisper import WhisperModel; WhisperModel('base.en', device='cpu', compute_type='int8'); print('==> Speech model OK')"
if command -v ollama >/dev/null 2>&1; then
  echo "==> Pulling local models (skipped quickly if already present)"
  ollama pull qwen3-vl:4b || echo "   could not pull qwen3-vl:4b (JAMES will use keyword rules)"
  ollama pull nomic-embed-text || echo "   could not pull nomic-embed-text (PAGE will use BM25 only)"
else
  echo "==> Ollama not found. JAMES still runs, with keyword rules and BM25 search."
fi
echo "==> Running tests"
python -m pytest -q tests
python -m james_core.pack check packs/pharma-utility | tail -1
python -m james_core.suite | sed -n '3,4p;$p'
echo ""
echo "Done. To start:  source .venv/bin/activate && python app.py"
