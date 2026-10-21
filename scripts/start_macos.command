#!/bin/zsh
set -e

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOCAL_ENV="$ROOT_DIR/.env.local"
URL="http://127.0.0.1:7865"

if [[ -f "$LOCAL_ENV" ]]; then
  set -a
  source "$LOCAL_ENV"
  set +a
fi

: "${INDEXTTS_REPO:=$ROOT_DIR/vendor/index-tts}"
: "${INDEXTTS_MODEL_DIR:=$ROOT_DIR/models/IndexTTS-2.5}"
: "${INDEXTTS_PYTHON:=$INDEXTTS_REPO/.venv/bin/python3}"

if /usr/bin/curl -fsS "$URL/api/status" >/dev/null 2>&1; then
  /usr/bin/open "$URL"
  exit 0
fi

if [[ ! -x "$INDEXTTS_PYTHON" ]]; then
  echo "找不到 IndexTTS Python：$INDEXTTS_PYTHON"
  echo "请复制 .env.example 为 .env.local，并填写真实路径。"
  exit 1
fi

if [[ ! -f "$INDEXTTS_MODEL_DIR/config.yaml" ]]; then
  echo "找不到模型配置：$INDEXTTS_MODEL_DIR/config.yaml"
  echo "请在 .env.local 中设置 INDEXTTS_MODEL_DIR。"
  exit 1
fi

cd "$ROOT_DIR"
export INDEXTTS_REPO INDEXTTS_MODEL_DIR
export PYTORCH_ENABLE_MPS_FALLBACK=0
export TOKENIZERS_PARALLELISM=false
export MPLCONFIGDIR="$ROOT_DIR/cache/matplotlib"
export XDG_CACHE_HOME="$ROOT_DIR/cache"

exec "$INDEXTTS_PYTHON" "$ROOT_DIR/easy_webui.py" --host 127.0.0.1 --port 7865 --open-browser

