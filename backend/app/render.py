"""Turning a SyncModel into ffmpeg filter chains, preview clips and exports."""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Literal, Optional

from .ffmpeg import FFMPEG, FfmpegError

PitchMode = Literal["preserve", "tape"]
PreviewMode = Literal["donor", "split"]

OUT_RATE = 48000


@dataclass
class SyncParams:
    """tB = alpha * tA + beta -- see analysis.py for the convention."""
    alpha: float = 1.0
    beta: float = 0.0
    pitch: PitchMode = "preserve"
    gain_db: float = 0.0


def _atempo_chain(alpha: float) -> list[str]:
    """atempo only accepts 0.5..100 per instance; split anything outside that."""
    stages: list[str] = []
    remaining = alpha
    while remaining < 0.5:
        stages.append("atempo=0.5")
        remaining /= 0.5
    while remaining > 100.0:
        stages.append("atempo=100")
        remaining /= 100.0
    if abs(remaining - 1.0) > 1e-9 or not stages:
        stages.append(f"atempo={remaining:.10f}")
    return stages


def speed_stages(alpha: float, pitch: PitchMode, in_rate: int = OUT_RATE) -> list[str]:
    """Play the donor audio `alpha` times faster (alpha < 1 => slower/longer)."""
    if abs(alpha - 1.0) < 1e-9:
        return []
    if pitch == "tape":
        # Reinterpret the sample rate, then resample back: speed and pitch move
        # together, which is exactly what undoes a PAL speed-up.
        return [f"asetrate={int(round(in_rate * alpha))}", f"aresample={in_rate}"]
    return _atempo_chain(alpha)


def build_filter(p: SyncParams, *, in_rate: int = OUT_RATE, label_in: str = "1:a:0",
                 label_out: str = "aout", duration: Optional[float] = None) -> str:
    """Full donor-audio chain: resample -> shift by beta -> scale by alpha."""
    stages = [f"aresample={in_rate}", "aformat=sample_fmts=fltp"]

    if p.beta > 1e-6:
        stages += [f"atrim=start={p.beta:.6f}", "asetpts=PTS-STARTPTS"]
    elif p.beta < -1e-6:
        stages += [f"adelay={int(round(abs(p.beta) * 1000))}:all=1"]

    stages += speed_stages(p.alpha, p.pitch, in_rate)

    if abs(p.gain_db) > 1e-6:
        stages.append(f"volume={p.gain_db:.3f}dB")

    stages.append("apad")
    if duration:
        stages.append(f"atrim=end={duration:.6f}")
    stages.append("asetpts=PTS-STARTPTS")

    return f"[{label_in}]" + ",".join(stages) + f"[{label_out}]"


# --------------------------------------------------------------------------
# preview
# --------------------------------------------------------------------------

def render_preview(
    *,
    video_a: str,
    donor_b: str,
    out_path: str,
    start: float,
    length: float,
    params: SyncParams,
    donor_stream: int = 0,
    keep_stream_a: int = 0,
    mode: PreviewMode = "donor",
    height: int = 480,
) -> str:
    """Short clip from A's picture with B's audio mapped onto it.

    mode="split" puts A's original audio hard left and the aligned donor hard
    right -- the fastest way to *hear* a drift that a chart only hints at.
    """
    donor_start = params.alpha * start + params.beta
    donor_len = params.alpha * length + 1.0

    lead_silence = 0.0
    if donor_start < 0:
        lead_silence = -donor_start
        donor_start = 0.0

    cmd = [
        FFMPEG, "-v", "error", "-nostdin", "-y",
        "-ss", f"{start:.6f}", "-t", f"{length:.6f}", "-i", video_a,
        "-ss", f"{donor_start:.6f}", "-t", f"{donor_len:.6f}", "-i", donor_b,
    ]

    donor = [f"aresample={OUT_RATE}", "aformat=sample_fmts=fltp:channel_layouts=stereo"]
    if lead_silence > 1e-6:
        donor.append(f"adelay={int(round(lead_silence * 1000))}:all=1")
    donor += speed_stages(params.alpha, params.pitch)
    if abs(params.gain_db) > 1e-6:
        donor.append(f"volume={params.gain_db:.3f}dB")
    donor.append("apad")
    donor.append(f"atrim=end={length:.6f}")
    donor.append("asetpts=PTS-STARTPTS")

    donor_chain = f"[1:a:{donor_stream}]" + ",".join(donor) + "[bd]"

    if mode == "split":
        orig_chain = (
            f"[0:a:{keep_stream_a}]aresample={OUT_RATE},"
            "aformat=sample_fmts=fltp:channel_layouts=mono,"
            f"apad,atrim=end={length:.6f},asetpts=PTS-STARTPTS[ao]"
        )
        down = "[bd]aformat=channel_layouts=mono[bm]"
        merge = "[ao][bm]amerge=inputs=2,aformat=channel_layouts=stereo[aout]"
        filtergraph = ";".join([donor_chain, orig_chain, down, merge])
    else:
        filtergraph = donor_chain + ";[bd]anull[aout]"

    cmd += [
        "-filter_complex", filtergraph,
        "-map", "0:v:0", "-map", "[aout]",
        "-vf", f"scale=-2:{height}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart",
        "-t", f"{length:.6f}",
        out_path,
    ]

    _exec(cmd)
    return out_path


# --------------------------------------------------------------------------
# export
# --------------------------------------------------------------------------

def render_export(
    *,
    video_a: str,
    donor_b: str,
    out_path: str,
    params: SyncParams,
    duration: float,
    donor_stream: int = 0,
    keep_original: bool = True,
    keep_stream_a: int = 0,
    audio_codec: str = "aac",
    audio_bitrate: str = "256k",
    donor_lang: str = "und",
    original_lang: str = "und",
    progress_cb=None,
) -> str:
    """Mux A's untouched video with the time-corrected donor audio."""
    filtergraph = build_filter(
        params, label_in=f"1:a:{donor_stream}", label_out="aout", duration=duration
    )

    cmd = [
        FFMPEG, "-v", "error", "-nostdin", "-y",
        "-progress", "pipe:1", "-stats_period", "0.5",
        "-i", video_a,
        "-i", donor_b,
        "-filter_complex", filtergraph,
        "-map", "0:v:0",
        "-map", "[aout]",
    ]
    if keep_original:
        cmd += ["-map", f"0:a:{keep_stream_a}"]

    cmd += [
        "-c:v", "copy",
        "-c:a", audio_codec, "-b:a", audio_bitrate,
        "-metadata:s:a:0", f"language={donor_lang}",
        "-metadata:s:a:0", "title=Synced donor",
        "-disposition:a:0", "default",
    ]
    if keep_original:
        cmd += [
            "-metadata:s:a:1", f"language={original_lang}",
            "-metadata:s:a:1", "title=Original",
            "-disposition:a:1", "0",
        ]

    # Matroska takes any subtitle codec; mp4 refuses the text formats most rips
    # carry, and a failed subtitle copy would sink the whole export
    if os.path.splitext(out_path)[1].lower() in (".mkv", ".mka"):
        cmd += ["-map", "0:s?", "-c:s", "copy"]

    cmd += ["-t", f"{duration:.6f}", out_path]

    _exec(cmd, progress_cb=progress_cb, total=duration)
    return out_path


def _exec(cmd: list[str], *, progress_cb=None, total: Optional[float] = None) -> None:
    if progress_cb is None:
        proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if proc.returncode != 0:
            raise FfmpegError(_tail(proc.stderr))
        return

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert proc.stdout is not None
    for line in proc.stdout:
        if line.startswith("out_time_ms=") and total:
            try:
                secs = int(line.split("=", 1)[1]) / 1_000_000.0
            except ValueError:
                continue
            progress_cb(max(0.0, min(1.0, secs / total)))
    proc.wait()
    if proc.returncode != 0:
        err = proc.stderr.read() if proc.stderr else ""
        raise FfmpegError(_tail(err.encode()))


def _tail(stderr: bytes) -> str:
    text = stderr.decode("utf-8", "replace").strip()
    return "\n".join(text.splitlines()[-25:]) or "ffmpeg failed"


def suggest_out_name(video_a: str, lang: str = "dub") -> str:
    base, ext = os.path.splitext(os.path.basename(video_a))
    if ext.lower() not in (".mkv", ".mp4", ".mov", ".webm"):
        ext = ".mkv"
    return f"{base}.{lang}-synced{ext}"
