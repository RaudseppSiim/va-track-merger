#!/usr/bin/env python3
"""Build a pair of test clips with a *known* drift, so the analyser can be checked.

    python tools/make_test_clips.py media/

Produces:
    test_A_23.976fps.mkv   picture we keep, slowed to 23.976 fps
    test_B_25fps.mkv       donor audio, 25 fps, starting 1.52 s later in the story

Ground truth:  tB = 0.959040 * tA - 1.520
"""
from __future__ import annotations

import os
import random
import shutil
import subprocess
import sys

FFMPEG = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"

DURATION = 90          # seconds of source material
FPS_SRC = 25.0
FPS_A = 24000 / 1001   # 23.976
TRIM_B = 1.52          # B starts this far in -- an exact 25 fps frame boundary
ALPHA = FPS_A / FPS_SRC

def _bed() -> str:
    """Irregular beeps across the whole clip: the music & effects bed that
    survives dubbing, and the only thing the audio method can lock onto."""
    rng = random.Random(1979)
    t, terms = 0.4, []
    while t < DURATION - 0.5:
        freq = rng.choice([220, 294, 370, 440, 587, 740, 880, 1175])
        terms.append(f"0.25*sin(2*PI*{freq}*t)*exp(-11*mod(t-{t:.2f},{DURATION}))")
        t += rng.uniform(0.9, 2.8)
    return "+".join(terms)


BED = _bed()


def run(args: list[str]) -> None:
    print("  ffmpeg", " ".join(args[1:6]), "…")
    proc = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        sys.exit(proc.stderr.decode("utf-8", "replace")[-2500:])


def main(outdir: str) -> None:
    os.makedirs(outdir, exist_ok=True)
    base = os.path.join(outdir, "_base.mkv")

    # ── 1. one shared master: chaotic picture + shared audio bed ──────────
    print("1/3  master")
    run([
        FFMPEG, "-v", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate={FPS_SRC}:duration={DURATION}",
        "-f", "lavfi", "-i", f"aevalsrc='{BED}':s=48000:d={DURATION}",
        "-vf", "random=frames=24:seed=7",     # non-periodic frame-to-frame motion
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-pix_fmt", "yuv420p",
        "-c:a", "pcm_s16le",
        "-t", str(DURATION), base,
    ])

    # ── 2. A: slowed to 23.976, "original language" on top of the bed ─────
    print("2/3  A (23.976 fps)")
    a_out = os.path.join(outdir, "test_A_23.976fps.mkv")
    run([
        FFMPEG, "-v", "error", "-y",
        "-i", base,
        "-f", "lavfi", "-i", "anoisesrc=d=200:c=pink:a=0.10:r=48000",
        "-filter_complex",
        f"[0:v]setpts=PTS/{ALPHA},fps={FPS_A}[v];"
        f"[0:a]atempo={ALPHA}[bed];"
        f"[1:a]atrim=0:{DURATION / ALPHA},volume=0.5[sp];"
        f"[bed][sp]amix=inputs=2:duration=first:normalize=0[a]",
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-pix_fmt", "yuv420p",
        "-r", f"{24000}/{1001}",
        "-c:a", "aac", "-b:a", "128k",
        "-metadata:s:a:0", "language=eng",
        a_out,
    ])

    # ── 3. B: untouched speed, starts 1.5 s later, different "language" ───
    print("3/3  B (25 fps)")
    b_out = os.path.join(outdir, "test_B_25fps.mkv")
    run([
        FFMPEG, "-v", "error", "-y",
        "-ss", str(TRIM_B), "-i", base,
        "-f", "lavfi", "-i", "anoisesrc=d=200:c=brown:a=0.12:r=48000",
        "-filter_complex",
        f"[1:a]atrim=0:{DURATION - TRIM_B},volume=0.6[sp];"
        f"[0:a][sp]amix=inputs=2:duration=first:normalize=0[a]",
        "-map", "0:v", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-metadata:s:a:0", "language=est",
        b_out,
    ])

    os.remove(base)
    print()
    print(f"Valmis: {a_out}")
    print(f"        {b_out}")
    print(f"Oodatav mudel:  alpha = {ALPHA:.6f}   beta = {-TRIM_B:.3f} s")
    print(f"Fixture'i enda maaramatus: ~{500 / FPS_A:.0f} ms (pool kaadrit A-s)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "media")
