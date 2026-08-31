#!/usr/bin/env bash
# Запускать НА СВОЁМ ПК, не в чужом sandbox.
set -euo pipefail

hr() { printf '\n%s\n' "----------------------------------------"; }

hr
echo "OS"
uname -a || true
if [[ -f /etc/os-release ]]; then . /etc/os-release; echo "$PRETTY_NAME"; fi

hr
echo "CPU"
if command -v lscpu >/dev/null 2>&1; then
  lscpu | grep -E 'Model name|Socket|Core|Thread|CPU\(s\)|Flags' | head -20
else
  sysctl -n machdep.cpu.brand_string 2>/dev/null || true
fi

hr
echo "RAM"
if command -v free >/dev/null 2>&1; then free -h; else
  vm_stat 2>/dev/null | head || true
fi

hr
echo "DISK"
df -h . 2>/dev/null || df -h

hr
echo "NVIDIA"
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv
else
  echo "nvidia-smi не найден"
fi

hr
echo "AMD (rocm / sysfs)"
if command -v rocm-smi >/dev/null 2>&1; then rocm-smi || true; fi
ls /sys/class/drm/card*/device/vendor 2>/dev/null || true

hr
echo "Ollama"
if command -v ollama >/dev/null 2>&1; then
  ollama --version || true
  ollama list || true
else
  echo "Ollama не установлена. https://ollama.com/download"
fi

hr
echo "ffmpeg"
command -v ffmpeg >/dev/null && ffmpeg -version | head -1 || echo "ffmpeg не найден — нужен для видео/голоса"

hr
echo "Рекомендация моделей (эвристика по nvidia-smi)"
VRAM_MIB=0
if command -v nvidia-smi >/dev/null 2>&1; then
  VRAM_MIB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -1 | tr -d ' ')
fi

pick() {
  echo "  text/VLM : $1"
  echo "  embed    : $2"
  echo "  whisper  : $3"
}

if [[ "${VRAM_MIB}" -ge 40000 ]]; then
  pick "qwen2.5vl:32b + qwen2.5:32b" "bge-m3" "large-v3"
elif [[ "${VRAM_MIB}" -ge 16000 ]]; then
  pick "qwen2.5vl:7b + qwen2.5:7b" "bge-m3" "large-v3"
elif [[ "${VRAM_MIB}" -ge 10000 ]]; then
  pick "qwen2.5vl:7b (Q4) + qwen2.5:7b" "bge-m3" "medium"
elif [[ "${VRAM_MIB}" -ge 7000 ]]; then
  pick "qwen2.5vl:3b + qwen2.5:7b-q4" "nomic-embed-text" "small"
else
  pick "qwen2.5:3b (CPU ok)" "nomic-embed-text" "base / tiny"
  echo "  GPU нет или VRAM маленькая — будет медленно, но пайплайн живой."
fi

echo
echo "Пропишите выбранные имена в configs/watch.yaml → llm.*"
