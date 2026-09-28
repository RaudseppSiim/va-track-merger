"""ffmpeg / ffprobe wrappers."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Optional

import numpy as np

FFMPEG = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = os.environ.get("FFPROBE_BIN") or shutil.which("ffprobe") or "ffprobe"


class FfmpegError(RuntimeError):
    pass


def _run(cmd: list[str], *, capture_stdout: bool = False) -> bytes:
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE if capture_stdout else subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-25:]
        raise FfmpegError("\n".join(tail) or f"exit {proc.returncode}")
    return proc.stdout or b""


@dataclass
class StreamInfo:
    index: int
    codec: str
    language: Optional[str] = None
    title: Optional[str] = None
    channels: Optional[int] = None
    sample_rate: Optional[int] = None


@dataclass
class MediaInfo:
    path: str
    duration: float
    container: str
    size_bytes: int
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None
    fps_ratio: Optional[str] = None
    video_codec: Optional[str] = None
    audio: list[StreamInfo] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "duration": self.duration,
            "container": self.container,
            "sizeBytes": self.size_bytes,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "fpsRatio": self.fps_ratio,
            "videoCodec": self.video_codec,
            "audio": [
                {
                    "index": s.index,
                    "codec": s.codec,
                    "language": s.language,
                    "title": s.title,
                    "channels": s.channels,
                    "sampleRate": s.sample_rate,
                }
                for s in self.audio
            ],
        }


def probe(path: str) -> MediaInfo:
    out = _run(
        [
            FFPROBE, "-v", "error",
            "-print_format", "json",
            "-show_format", "-show_streams",
            path,
        ],
        capture_stdout=True,
    )
    data = json.loads(out)
    fmt = data.get("format", {})

    info = MediaInfo(
        path=path,
        duration=float(fmt.get("duration") or 0.0),
        container=fmt.get("format_name", ""),
        size_bytes=int(fmt.get("size") or 0),
    )

    audio_ordinal = 0
    for st in data.get("streams", []):
        kind = st.get("codec_type")
        if kind == "video" and info.video_codec is None:
            info.video_codec = st.get("codec_name")
            info.width = st.get("width")
            info.height = st.get("height")
            rate = st.get("avg_frame_rate") or st.get("r_frame_rate") or "0/0"
            info.fps_ratio = rate
            try:
                frac = Fraction(rate)
                info.fps = float(frac) if frac.denominator else None
            except (ZeroDivisionError, ValueError):
                info.fps = None
            if not info.duration:
                info.duration = float(st.get("duration") or 0.0)
        elif kind == "audio":
            tags = st.get("tags") or {}
            info.audio.append(
                StreamInfo(
                    index=audio_ordinal,
                    codec=st.get("codec_name", ""),
                    language=tags.get("language"),
                    title=tags.get("title"),
                    channels=st.get("channels"),
                    sample_rate=int(st["sample_rate"]) if st.get("sample_rate") else None,
                )
            )
            audio_ordinal += 1

    return info


def decode_audio_mono(path: str, stream: int = 0, sample_rate: int = 8000) -> np.ndarray:
    """Decode one audio stream to mono float32 at `sample_rate`."""
    raw = _run(
        [
            FFMPEG, "-v", "error", "-nostdin",
            "-i", path,
            "-map", f"0:a:{stream}",
            "-vn", "-ac", "1", "-ar", str(sample_rate),
            "-f", "f32le", "-",
        ],
        capture_stdout=True,
    )
    return np.frombuffer(raw, dtype="<f4")


def decode_video_gray(path: str, fps: float = 8.0, width: int = 32, height: int = 18) -> np.ndarray:
    """Decode video as a stack of tiny grayscale frames -> (n_frames, height*width) uint8."""
    raw = _run(
        [
            FFMPEG, "-v", "error", "-nostdin",
            "-i", path,
            "-an", "-sn", "-dn",
            "-vf", f"fps={fps},scale={width}:{height}:flags=area,format=gray",
            "-f", "rawvideo", "-pix_fmt", "gray", "-",
        ],
        capture_stdout=True,
    )
    px = width * height
    n = len(raw) // px
    if n == 0:
        return np.zeros((0, px), dtype=np.uint8)
    return np.frombuffer(raw[: n * px], dtype=np.uint8).reshape(n, px)


def has_video_stream(info: MediaInfo) -> bool:
    return info.video_codec is not None
