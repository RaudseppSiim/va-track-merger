"""Signal extraction + drift measurement.

Convention used everywhere in this file:

    A = target video (the picture we keep)
    B = donor video (the foreign-language audio we want)

    content visible at time t in A is visible at time  t + d(t)  in B
    linear model:  tB = alpha * tA + beta   =>   d(t) = (alpha - 1) * t + beta

`alpha` is therefore also the atempo factor needed to stretch B's audio onto
A's timeline (alpha < 1 slows B down, alpha > 1 speeds it up).
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Literal, Optional

import numpy as np

from . import ffmpeg

SIGNAL_HZ = 50.0  # analysis grid: 20 ms per sample


# --------------------------------------------------------------------------
# signal extraction
# --------------------------------------------------------------------------

def _smooth(x: np.ndarray, width: int = 3) -> np.ndarray:
    """Widen one-sample spikes into small bumps.

    Refinement resamples one signal onto the other's clock, and linear
    interpolation attenuates a needle-thin spike by an amount that depends on
    where it falls between two samples. On a sparse onset train that alone is
    enough to destroy the correlation peak, so every signal is blurred to a few
    samples wide first. The peak position is unaffected -- both sides get the
    same blur -- and sub-sample interpolation still recovers millisecond detail.
    """
    if width < 2 or x.size < width * 2:
        return x
    kernel = np.hanning(width + 2)[1:-1].astype(np.float32)
    kernel /= kernel.sum()
    return np.convolve(x, kernel, mode="same").astype(np.float32)


def _normalise(x: np.ndarray) -> np.ndarray:
    if x.size == 0:
        return x
    x = x - np.median(x)
    scale = np.percentile(np.abs(x), 95)
    if scale > 1e-9:
        x = x / scale
    return x.astype(np.float32)


N_BANDS = 10
FFT_WIN = 512


def _band_edges(sr: int, n_bins: int) -> list[tuple[int, int]]:
    """Log-spaced band edges from 40 Hz to just under Nyquist."""
    lo, hi = 40.0, sr * 0.45
    cuts = np.geomspace(lo, hi, N_BANDS + 1)
    idx = np.clip((cuts / (sr / 2.0) * (n_bins - 1)).astype(int), 0, n_bins - 1)
    return [(int(idx[i]), int(max(idx[i + 1], idx[i] + 1))) for i in range(N_BANDS)]


def audio_novelty(path: str, stream: int = 0, sr: int = 8000) -> np.ndarray:
    """Multi-band spectral flux: the onset curve the correlator works best on.

    A broadband RMS derivative is cheap but it drowns: continuous dialogue or
    room tone masks the transients that actually carry the alignment. Splitting
    into log-spaced bands first means a tonal hit still shows up even while
    something noisy is playing over it -- which is the normal case in a dub,
    where the speech differs but the music & effects bed does not.
    """
    samples = ffmpeg.decode_audio_mono(path, stream=stream, sample_rate=sr)
    hop = int(round(sr / SIGNAL_HZ))
    n = (samples.size - FFT_WIN) // hop + 1
    if samples.size == 0 or n <= 2:
        return np.zeros(0, dtype=np.float32)

    window = np.hanning(FFT_WIN).astype(np.float32)
    bands = _band_edges(sr, FFT_WIN // 2 + 1)
    energy = np.empty((n, N_BANDS), dtype=np.float32)

    # chunked so a feature-length file does not need a full spectrogram in RAM
    CHUNK = 16384
    for base in range(0, n, CHUNK):
        count = min(CHUNK, n - base)
        starts = (base + np.arange(count)) * hop
        block = samples[starts[:, None] + np.arange(FFT_WIN)] * window
        mag = np.abs(np.fft.rfft(block, axis=1)).astype(np.float32)
        for b, (i0, i1) in enumerate(bands):
            energy[base:base + count, b] = mag[:, i0:i1].mean(axis=1)

    flux = np.diff(np.log1p(energy * 100.0), axis=0)
    np.maximum(flux, 0.0, out=flux)
    novelty = np.concatenate(([0.0], flux.sum(axis=1))).astype(np.float32)
    return _normalise(_smooth(novelty, 3))


def audio_peaks(path: str, stream: int = 0, sr: int = 8000, hz: float = 200.0):
    """(min, max) pairs per bucket, for drawing the waveform."""
    samples = ffmpeg.decode_audio_mono(path, stream=stream, sample_rate=sr)
    if samples.size == 0:
        return np.zeros(0, dtype=np.float32), np.zeros(0, dtype=np.float32)

    hop = max(1, int(round(sr / hz)))
    n = samples.size // hop
    if n == 0:
        return np.zeros(0, dtype=np.float32), np.zeros(0, dtype=np.float32)

    frames = samples[: n * hop].reshape(n, hop)
    return frames.min(axis=1).astype(np.float32), frames.max(axis=1).astype(np.float32)


def video_motion(path: str) -> np.ndarray:
    """Frame-to-frame change energy: identical for two encodes of one edit, and
    completely blind to which language is on the soundtrack."""
    frames = ffmpeg.decode_video_gray(path, fps=SIGNAL_HZ / 4.0, width=32, height=18)
    if frames.shape[0] < 2:
        return np.zeros(0, dtype=np.float32)

    diff = np.abs(np.diff(frames.astype(np.float32), axis=0)).mean(axis=1)
    diff = np.concatenate(([diff[0]], diff))

    # the video grid is SIGNAL_HZ/4, lift it onto the common grid
    idx = np.linspace(0, diff.size - 1, diff.size * 4)
    upsampled = np.interp(idx, np.arange(diff.size), diff)
    return _normalise(_smooth(upsampled.astype(np.float32), 3))


# --------------------------------------------------------------------------
# correlation
# --------------------------------------------------------------------------

def normalised_xcorr(template: np.ndarray, signal: np.ndarray) -> np.ndarray:
    """NCC of `template` slid over `signal`; result[k] scores signal[k:k+n]."""
    n = template.size
    m = signal.size - n + 1
    if m <= 0:
        return np.zeros(0, dtype=np.float32)

    t = template.astype(np.float64)
    t = t - t.mean()
    t_norm = float(np.sqrt((t * t).sum()))
    if t_norm < 1e-12:
        return np.zeros(m, dtype=np.float32)

    s = signal.astype(np.float64)
    size = 1 << int(np.ceil(np.log2(s.size + n)))
    conv = np.fft.irfft(np.fft.rfft(t[::-1], size) * np.fft.rfft(s, size), size)
    num = conv[n - 1: n - 1 + m]

    cs = np.concatenate(([0.0], np.cumsum(s)))
    cs2 = np.concatenate(([0.0], np.cumsum(s * s)))
    win_sum = cs[n:n + m] - cs[:m]
    win_sq = cs2[n:n + m] - cs2[:m]
    var = np.maximum(win_sq - win_sum * win_sum / n, 1e-12)

    return (num / (t_norm * np.sqrt(var))).astype(np.float32)


def _refine_peak(scores: np.ndarray, k: int) -> float:
    """Parabolic sub-sample interpolation around the integer peak `k`."""
    if k <= 0 or k >= scores.size - 1:
        return float(k)
    y0, y1, y2 = float(scores[k - 1]), float(scores[k]), float(scores[k + 1])
    denom = y0 - 2.0 * y1 + y2
    if abs(denom) < 1e-12:
        return float(k)
    return k + 0.5 * (y0 - y2) / denom


@dataclass
class Measurement:
    t: float          # window centre on A's timeline (seconds)
    offset: float     # d(t): seconds to add to tA to land on B
    score: float      # peak NCC, 0..1
    margin: float     # peak minus best competing peak: how unique the match is
    used: bool = True


def measure_offsets(
    sig_a: np.ndarray,
    sig_b: np.ndarray,
    *,
    windows: int = 24,
    window_sec: float = 24.0,
    max_shift_sec: float = 30.0,
    hz: float = SIGNAL_HZ,
) -> list[Measurement]:
    """Slide `windows` probes across A and locate each one inside B."""
    out: list[Measurement] = []
    if sig_a.size == 0 or sig_b.size == 0:
        return out

    win = int(window_sec * hz)
    lag = int(max_shift_sec * hz)
    dur_a = sig_a.size / hz
    if win >= sig_a.size:
        win = max(int(sig_a.size * 0.5), int(2 * hz))
    if win < int(2 * hz):
        return out

    half = (win / hz) / 2.0
    lo = half
    hi = max(lo + 1e-3, dur_a - half)
    centres = np.linspace(lo, hi, windows) if windows > 1 else np.array([(lo + hi) / 2.0])

    for tc in centres:
        i0 = int(round(tc * hz)) - win // 2
        i0 = max(0, min(i0, sig_a.size - win))
        template = sig_a[i0:i0 + win]

        b0 = max(0, i0 - lag)
        b1 = min(sig_b.size, i0 + win + lag)
        if b1 - b0 < win + 2:
            continue

        scores = normalised_xcorr(template, sig_b[b0:b1])
        if scores.size == 0:
            continue

        k = int(np.argmax(scores))
        peak = float(scores[k])

        guard = max(1, int(0.5 * hz))
        masked = scores.copy()
        masked[max(0, k - guard): k + guard + 1] = -np.inf
        runner_up = float(np.max(masked)) if np.isfinite(masked).any() else 0.0

        k_ref = _refine_peak(scores, k)
        offset = (b0 + k_ref - i0) / hz

        out.append(
            Measurement(
                t=float((i0 + win / 2.0) / hz),
                offset=float(offset),
                score=peak,
                margin=float(peak - runner_up),
            )
        )

    return out


# --------------------------------------------------------------------------
# model fitting
# --------------------------------------------------------------------------

@dataclass
class SyncModel:
    alpha: float               # tB = alpha * tA + beta
    beta: float
    rmsResidualMs: float
    maxResidualMs: float
    inliers: int
    total: int
    confidence: float          # 0..1, blunt heuristic behind the UI traffic light
    note: str = ""

    def offset_at(self, t: float) -> float:
        return (self.alpha - 1.0) * t + self.beta

    def as_dict(self) -> dict:
        return asdict(self)


ALPHA_RANGE = (0.80, 1.25)


def fit_linear(
    measurements: list[Measurement],
    *,
    min_score: float = 0.15,
    tol: float = 0.060,
    lock_alpha: Optional[float] = None,
) -> SyncModel:
    """Robust line fit: RANSAC for the consensus set, weighted LSQ for precision.

    A plain least-squares fit is useless here. Cross-correlation on repetitive
    footage produces a minority of confident-looking matches that are seconds
    away from the truth, and those drag a median-seeded fit off the line, so the
    consensus has to be found by voting rather than by trimming.

    Mutates `used` on each measurement so the UI can grey out rejected probes.
    """
    for m in measurements:
        m.used = False

    pts = [m for m in measurements if m.score >= min_score]
    if not pts:
        return SyncModel(1.0, 0.0, 0.0, 0.0, 0, len(measurements), 0.0,
                         "Usaldusväärseid vasteid ei leitud.")

    t = np.array([m.t for m in pts], dtype=np.float64)
    d = np.array([m.offset for m in pts], dtype=np.float64)
    w = np.array([max(m.score, 0.05) for m in pts], dtype=np.float64)

    slope, intercept, keep = _ransac(t, d, w, tol=tol, lock_alpha=lock_alpha)

    # polish on the consensus set, then re-test membership once
    for _ in range(3):
        if keep.sum() < 2:
            break
        slope, intercept = _weighted_lsq(t[keep], d[keep], w[keep], lock_alpha)
        new_keep = np.abs(d - (slope * t + intercept)) <= tol
        if new_keep.sum() < 2 or np.array_equal(new_keep, keep):
            break
        keep = new_keep

    resid = d - (slope * t + intercept)
    rms = float(np.sqrt((resid[keep] ** 2).mean())) if keep.any() else 0.0
    mx = float(np.abs(resid[keep]).max()) if keep.any() else 0.0

    for m, ok in zip(pts, keep):
        m.used = bool(ok)

    inliers = int(keep.sum())
    span = float(t[keep].max() - t[keep].min()) if inliers >= 2 else 0.0
    mean_score = float(w[keep].mean()) if inliers else 0.0

    confidence = min(1.0, mean_score / 0.45)
    confidence *= inliers / max(len(measurements), 1)      # agreement
    confidence *= min(1.0, inliers / 6.0)                  # enough votes
    confidence *= min(1.0, span / 90.0) if span else 0.15  # spread over the film
    confidence *= 1.0 / (1.0 + rms / 0.050)
    confidence = max(0.0, min(1.0, confidence))

    return SyncModel(
        alpha=float(1.0 + slope),
        beta=float(intercept),
        rmsResidualMs=rms * 1000.0,
        maxResidualMs=mx * 1000.0,
        inliers=inliers,
        total=len(measurements),
        confidence=confidence,
    )


def _weighted_lsq(t, d, w, lock_alpha: Optional[float]) -> tuple[float, float]:
    if lock_alpha is not None:
        slope = lock_alpha - 1.0
        return slope, float(np.average(d - slope * t, weights=w))
    sw = w.sum()
    mt = float((w * t).sum() / sw)
    md = float((w * d).sum() / sw)
    var = float((w * (t - mt) ** 2).sum())
    slope = 0.0 if var < 1e-9 else float((w * (t - mt) * (d - md)).sum() / var)
    return slope, md - slope * mt


def _ransac(t, d, w, *, tol: float, lock_alpha: Optional[float]):
    """Vote for the line that the most probe windows agree on."""
    n = t.size
    span = float(t.max() - t.min()) if n > 1 else 0.0
    min_gap = max(span * 0.25, 1e-6)

    candidates: list[tuple[float, float]] = []
    if lock_alpha is not None:
        slope = lock_alpha - 1.0
        candidates = [(slope, float(d[i] - slope * t[i])) for i in range(n)]
    else:
        for i in range(n):
            candidates.append((0.0, float(d[i])))          # pure offset, no drift
            for j in range(i + 1, n):
                gap = t[j] - t[i]
                if gap < min_gap:
                    continue
                slope = (d[j] - d[i]) / gap
                if not (ALPHA_RANGE[0] - 1.0 <= slope <= ALPHA_RANGE[1] - 1.0):
                    continue
                candidates.append((float(slope), float(d[i] - slope * t[i])))

    best = (-1.0, 0.0, 0.0, np.zeros(n, dtype=bool))
    for slope, intercept in candidates:
        inl = np.abs(d - (slope * t + intercept)) <= tol
        if not inl.any():
            continue
        votes = float(w[inl].sum())
        # a wider spread of agreeing probes is worth more than a tight cluster
        spread = float(t[inl].max() - t[inl].min()) if inl.sum() > 1 else 0.0
        votes *= 1.0 + 0.5 * (spread / span if span else 0.0)
        if votes > best[0]:
            best = (votes, slope, intercept, inl)

    if best[0] < 0:
        slope = 0.0 if lock_alpha is None else lock_alpha - 1.0
        return slope, float(np.average(d - slope * t, weights=w)), np.ones(n, dtype=bool)

    # Thin consensus is not corrected here -- the best available line is still
    # the best available line. It is penalised in `confidence`, which carries
    # the inlier fraction, so a coincidental 4-of-24 agreement scores near zero
    # and loses to the other method instead of being silently trusted.
    return best[1], best[2], best[3]


# --------------------------------------------------------------------------
# iterative refinement
# --------------------------------------------------------------------------

def warp_signal(sig_b: np.ndarray, alpha: float, beta: float, n_out: int,
                hz: float = SIGNAL_HZ) -> np.ndarray:
    """Resample B onto A's timeline through the current model: W(t) = B(alpha*t + beta)."""
    t = np.arange(n_out, dtype=np.float64) / hz
    src = (alpha * t + beta) * hz
    return np.interp(src, np.arange(sig_b.size), sig_b, left=0.0, right=0.0).astype(np.float32)


def analyse_pair(
    sig_a: np.ndarray,
    sig_b: np.ndarray,
    *,
    windows: int = 24,
    window_sec: float = 24.0,
    max_shift_sec: float = 30.0,
    lock_alpha: Optional[float] = None,
    refine_passes: int = 2,
    progress=None,
) -> tuple[SyncModel, list[Measurement]]:
    """Coarse search, then measure again on a de-drifted copy of B.

    A 4 % rate mismatch smears a 20 s correlation window by +-0.4 s, which caps
    precision at roughly +-150 ms no matter how good the signal is. Warping B
    through the coarse model removes almost all of that, so the refining passes
    can use long windows and still resolve tens of milliseconds.
    """
    if sig_a.size == 0 or sig_b.size == 0:
        return SyncModel(1.0, 0.0, 0.0, 0.0, 0, 0, 0.0, "Signaal on tühi."), []

    # A probe window of length W sees the two files drift apart by W*(1-alpha)
    # across its own span -- 0.32 s over 8 s at PAL rates. Features narrower
    # than that simply miss each other at every single lag, so the first pass
    # runs on a deliberately blurred copy: coarse enough that a transient still
    # overlaps its counterpart despite the stretch, which is all the coarse pass
    # has to get right. The refining passes put the sharpness back once the rate
    # is known and the residual stretch is negligible.
    coarse_win = min(window_sec, 6.0)
    blur = max(3, int(round(coarse_win * SIGNAL_HZ * 0.06)))
    coarse = measure_offsets(
        _smooth(sig_a, blur), _smooth(sig_b, blur),
        windows=max(windows, 24),
        window_sec=coarse_win,
        max_shift_sec=max_shift_sec,
    )
    model = fit_linear(coarse, tol=0.250, lock_alpha=lock_alpha)
    measurements = coarse
    if progress:
        progress(0.4)

    if model.inliers < 2:
        return model, measurements

    for step in range(refine_passes):
        # sharpen and narrow the search as the model gets closer
        blur = max(1, 7 >> (step + 1))
        tol = 0.080 if step == 0 else 0.050
        warped = warp_signal(sig_b, model.alpha, model.beta, sig_a.size)
        residual = measure_offsets(
            _smooth(sig_a, blur), _smooth(warped, blur),
            windows=windows,
            window_sec=window_sec,
            max_shift_sec=max(1.0, 4.0 / (step + 1)),
        )
        if not residual:
            break

        # r(t) lives on the warped timeline; lift it back to B's
        absolute = [
            Measurement(
                t=m.t,
                offset=model.offset_at(m.t) + model.alpha * m.offset,
                score=m.score,
                margin=m.margin,
            )
            for m in residual
        ]
        candidate = fit_linear(absolute, tol=tol, lock_alpha=lock_alpha)
        # A refining pass may only sharpen the answer, never redefine it: if it
        # comes back with a thinner consensus it has latched onto a side peak,
        # and the coarse model was the honest one.
        if candidate.inliers < 2 or candidate.inliers < model.inliers * 0.6:
            break
        model, measurements = candidate, absolute
        if progress:
            progress(0.4 + 0.6 * (step + 1) / refine_passes)

    return model, measurements


# --------------------------------------------------------------------------
# framerate reasoning
# --------------------------------------------------------------------------

KNOWN_RATIOS: list[tuple[str, float]] = [
    ("1:1 (sama kiirus)", 1.0),
    ("PAL speedup: 25 -> 23.976", 23.976 / 25.0),
    ("PAL slowdown: 23.976 -> 25", 25.0 / 23.976),
    ("25 -> 24", 24.0 / 25.0),
    ("24 -> 25", 25.0 / 24.0),
    ("NTSC pulldown: 24 -> 23.976", 23.976 / 24.0),
    ("NTSC: 23.976 -> 24", 24.0 / 23.976),
    ("30 -> 29.97", 29.97 / 30.0),
    ("29.97 -> 30", 30.0 / 29.97),
]


def classify_ratio(alpha: float, tolerance: float = 0.0008) -> Optional[dict]:
    best = None
    for name, value in KNOWN_RATIOS:
        err = abs(alpha - value)
        if err <= tolerance and (best is None or err < best["error"]):
            best = {"name": name, "value": value, "error": err}
    return best


def ratio_from_fps(fps_a: Optional[float], fps_b: Optional[float]) -> Optional[float]:
    """alpha implied purely by the two container framerates."""
    if not fps_a or not fps_b:
        return None
    return fps_a / fps_b


Method = Literal["video", "audio"]
