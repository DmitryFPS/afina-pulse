#!/usr/bin/env bash
set -euo pipefail

if ! command -v ollama >/dev/null 2>&1; then
  echo "Сначала установите Ollama: https://ollama.com/download"
  exit 1
fi

TEXT="${WATCH_LLM_MODEL:-qwen2.5:7b}"
VLM="${WATCH_VLM_MODEL:-qwen2.5vl:7b}"
EMB="${WATCH_EMBED_MODEL:-bge-m3}"

echo "Pull $TEXT"
ollama pull "$TEXT" || ollama pull qwen2.5:3b

echo "Pull $VLM"
ollama pull "$VLM" || ollama pull qwen2.5vl:3b || true

echo "Pull $EMB"
ollama pull "$EMB" || ollama pull nomic-embed-text

echo "Готово. ollama list:"
ollama list
