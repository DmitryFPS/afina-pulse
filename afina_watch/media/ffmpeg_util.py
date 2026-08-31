from __future__ import annotations

import subprocess
from pathlib import Path


def run_ffmpeg(args: list[str]) -> None:
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "ffmpeg failed")


def extract_audio(src: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(["-i", str(src), "-vn", "-ac", "1", "-ar", "16000", str(dest)])
    return dest


def extract_frames(src: Path, dest_dir: Path, max_frames: int, max_seconds: int) -> list[Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    # равномерная сетка кадров, не больше max_frames за первые max_seconds
    pattern = dest_dir / "frame_%03d.jpg"
    vf = f"fps=1/{max(1, max_seconds // max(1, max_frames))},scale=768:-2"
    run_ffmpeg(
        [
            "-i",
            str(src),
            "-t",
            str(max_seconds),
            "-vf",
            vf,
            "-frames:v",
            str(max_frames),
            str(pattern),
        ]
    )
    return sorted(dest_dir.glob("frame_*.jpg"))
