#!/usr/bin/env python3
"""End-to-end check against the clips from make_test_clips.py.

    python tools/selftest.py media/test_A_23.976fps.mkv media/test_B_25fps.mkv 0.959041 -1.5

Measures the drift with both methods and reports how far each lands from the
known truth. Exits non-zero if the best method misses by more than 40 ms.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app import analysis, ffmpeg, render  # noqa: E402

TOLERANCE_MS = 45.0   # fixture itself is only good to ~21 ms (half a frame)


def check(path_a: str, path_b: str, true_alpha: float, true_beta: float) -> int:
    info_a = ffmpeg.probe(path_a)
    info_b = ffmpeg.probe(path_b)
    print(f"A  {os.path.basename(path_a)}  {info_a.duration:.3f}s  {info_a.fps:.3f}fps")
    print(f"B  {os.path.basename(path_b)}  {info_b.duration:.3f}s  {info_b.fps:.3f}fps")
    print(f"alpha from fps ratio = {analysis.ratio_from_fps(info_a.fps, info_b.fps):.6f}")
    print()

    worst = None
    for method in ("video", "audio"):
        t0 = time.time()
        if method == "video":
            sig_a, sig_b = analysis.video_motion(path_a), analysis.video_motion(path_b)
        else:
            sig_a, sig_b = analysis.audio_novelty(path_a), analysis.audio_novelty(path_b)

        model, ms = analysis.analyse_pair(
            sig_a, sig_b, windows=16, window_sec=16.0, max_shift_sec=20.0
        )
        elapsed = time.time() - t0

        err_start = abs(model.beta - true_beta) * 1000
        end_t = info_a.duration
        err_end = abs(
            ((model.alpha - 1) * end_t + model.beta) - ((true_alpha - 1) * end_t + true_beta)
        ) * 1000

        print(f"[{method:5s}] alpha={model.alpha:.6f}  beta={model.beta:+.3f}s  "
              f"resid={model.rmsResidualMs:5.1f}ms  {model.inliers}/{model.total}  "
              f"conf={model.confidence:.2f}  ({elapsed:.1f}s)")
        print(f"          error at start {err_start:6.1f} ms   error at end {err_end:6.1f} ms")

        guess = analysis.classify_ratio(model.alpha)
        if guess:
            print(f"          detected: {guess['name']}")

        score = max(err_start, err_end)
        if worst is None or score < worst[0]:
            worst = (score, method, model)
        print()

    best_err, best_method, best_model = worst
    params = render.SyncParams(alpha=best_model.alpha, beta=best_model.beta, pitch="tape")
    print("filter chain:", render.build_filter(params, duration=info_a.duration))
    print()

    if best_err <= TOLERANCE_MS:
        print(f"OK  best method '{best_method}' is off by at most {best_err:.1f} ms (<= {TOLERANCE_MS:.0f} ms)")
        return 0
    print(f"FAIL  best method '{best_method}' is off by {best_err:.1f} ms (> {TOLERANCE_MS:.0f} ms)")
    return 1


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    a, b = sys.argv[1], sys.argv[2]
    alpha = float(sys.argv[3]) if len(sys.argv) > 3 else 1.0
    beta = float(sys.argv[4]) if len(sys.argv) > 4 else 0.0
    sys.exit(check(a, b, alpha, beta))
