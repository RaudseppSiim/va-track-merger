#!/usr/bin/env python3
"""Check an exported file by measuring its own two audio tracks against each other.

    python tools/verify_export.py work/exports/whatever.mkv

Track 0 is the synced donor, track 1 the original that came with the picture.
If the merge worked they now share one clock, so the drift between them must be
flat and near zero -- this is the same measurement the tool performs, turned on
its own output.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app import analysis, ffmpeg  # noqa: E402


def main(path: str) -> int:
    info = ffmpeg.probe(path)
    if len(info.audio) < 2:
        sys.exit("The file has only one audio track -- export with 'Keep original audio' enabled.")

    print(f"{os.path.basename(path)}  {info.duration:.2f}s  {len(info.audio)} audio tracks")
    donor = analysis.audio_novelty(path, stream=0)
    original = analysis.audio_novelty(path, stream=1)

    model, ms = analysis.analyse_pair(
        original, donor, windows=16, window_sec=16.0, max_shift_sec=5.0
    )
    start_ms = model.beta * 1000
    end_ms = ((model.alpha - 1) * info.duration + model.beta) * 1000

    print(f"track 0 vs track 1:  alpha={model.alpha:.6f}  beta={model.beta:+.4f}s")
    print(f"  offset at start {start_ms:+7.1f} ms")
    print(f"  offset at end   {end_ms:+7.1f} ms")
    print(f"  residual {model.rmsResidualMs:.1f} ms   {model.inliers}/{model.total} points   "
          f"confidence {model.confidence:.2f}")

    worst = max(abs(start_ms), abs(end_ms))
    if model.confidence < 0.2:
        print("\nUNCLEAR: no trustworthy match found between the tracks.")
        return 2
    if worst <= 40:
        print(f"\nOK  the tracks stay within {worst:.0f} ms of each other -- in sync.")
        return 0
    print(f"\nFAIL  the tracks diverge by {worst:.0f} ms.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "work/exports/test_synced.mkv"))
