"""HTTP API for the video/audio track merger."""
from __future__ import annotations

import os
import re
import time
from typing import Literal, Optional

import numpy as np
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import analysis, cache, ffmpeg, render
from .jobs import Job, registry

MEDIA_DIR = os.path.abspath(os.environ.get("MEDIA_DIR", "./media"))
WORK_DIR = os.path.abspath(os.environ.get("WORK_DIR", "./work"))
PREVIEW_DIR = os.path.join(WORK_DIR, "previews")
EXPORT_DIR = os.path.join(WORK_DIR, "exports")
FRONTEND_DIR = os.path.abspath(
    os.environ.get("FRONTEND_DIR", os.path.join(os.path.dirname(__file__), "..", "..", "frontend"))
)

VIDEO_EXT = {".mkv", ".mp4", ".mov", ".avi", ".webm", ".m4v", ".ts", ".mpg", ".mpeg", ".wmv", ".flv"}
AUDIO_EXT = {".mka", ".mp3", ".aac", ".ac3", ".eac3", ".dts", ".flac", ".wav", ".m4a", ".opus", ".ogg"}

app = FastAPI(title="Video/Audio Track Merger", version="1.0.0")

for d in (WORK_DIR, PREVIEW_DIR, EXPORT_DIR):
    os.makedirs(d, exist_ok=True)


# --------------------------------------------------------------------------
# path safety
# --------------------------------------------------------------------------

ROOTS = [MEDIA_DIR, WORK_DIR]


def resolve(path: str) -> str:
    """Accept a path relative to MEDIA_DIR, or an absolute path under a root."""
    candidate = path if os.path.isabs(path) else os.path.join(MEDIA_DIR, path)
    real = os.path.realpath(candidate)
    for root in ROOTS:
        if real == root or real.startswith(root + os.sep):
            if not os.path.exists(real):
                raise HTTPException(404, f"Faili ei leitud: {path}")
            return real
    raise HTTPException(403, f"Tee on lubatud kaustadest väljas: {path}")


def rel(path: str) -> str:
    for root in ROOTS:
        if path.startswith(root + os.sep):
            return os.path.relpath(path, root).replace(os.sep, "/")
    return path.replace(os.sep, "/")


# --------------------------------------------------------------------------
# request models
# --------------------------------------------------------------------------

class AnalyseReq(BaseModel):
    pathA: str
    pathB: str
    streamA: int = 0
    streamB: int = 0
    method: Literal["video", "audio", "both"] = "both"
    windows: int = Field(24, ge=3, le=200)
    windowSec: float = Field(24.0, ge=2.0, le=180.0)
    maxShiftSec: float = Field(30.0, ge=0.5, le=600.0)
    lockAlpha: Optional[float] = None


class RefitReq(BaseModel):
    measurements: list[dict]
    lockAlpha: Optional[float] = None
    minScore: float = 0.15
    tol: float = 0.060


class ProbePointReq(BaseModel):
    pathA: str
    pathB: str
    streamA: int = 0
    streamB: int = 0
    method: Literal["video", "audio"] = "video"
    t: float
    windowSec: float = 12.0
    maxShiftSec: float = 3.0
    alpha: float = 1.0
    beta: float = 0.0


class SyncBody(BaseModel):
    alpha: float = 1.0
    beta: float = 0.0
    pitch: Literal["preserve", "tape"] = "preserve"
    gainDb: float = 0.0

    def to_params(self) -> render.SyncParams:
        return render.SyncParams(alpha=self.alpha, beta=self.beta,
                                 pitch=self.pitch, gain_db=self.gainDb)


class PreviewReq(BaseModel):
    pathA: str
    pathB: str
    streamA: int = 0
    streamB: int = 0
    start: float = 0.0
    length: float = Field(12.0, ge=1.0, le=120.0)
    mode: Literal["donor", "split"] = "split"
    height: int = Field(480, ge=180, le=1080)
    sync: SyncBody = SyncBody()


class ExportReq(BaseModel):
    pathA: str
    pathB: str
    streamA: int = 0
    streamB: int = 0
    outName: Optional[str] = None
    keepOriginal: bool = True
    audioCodec: str = "aac"
    audioBitrate: str = "256k"
    donorLang: str = "und"
    originalLang: str = "und"
    sync: SyncBody = SyncBody()


# --------------------------------------------------------------------------
# library + probing
# --------------------------------------------------------------------------

@app.get("/api/health")
def health() -> dict:
    try:
        ffmpeg._run([ffmpeg.FFMPEG, "-version"], capture_stdout=True)
        ok = True
        err = None
    except Exception as exc:  # noqa: BLE001
        ok, err = False, str(exc)
    return {
        "ok": ok,
        "ffmpegError": err,
        "mediaDir": MEDIA_DIR,
        "workDir": WORK_DIR,
        "exportDir": EXPORT_DIR,
    }


@app.get("/api/files")
def list_files() -> dict:
    items = []
    for root in ROOTS:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in ("cache", "previews", ".git")]
            for name in sorted(filenames):
                ext = os.path.splitext(name)[1].lower()
                if ext not in VIDEO_EXT and ext not in AUDIO_EXT:
                    continue
                full = os.path.join(dirpath, name)
                try:
                    size = os.path.getsize(full)
                except OSError:
                    continue
                items.append({
                    "path": rel(full),
                    "abs": full,
                    "name": name,
                    "dir": rel(dirpath),
                    "sizeBytes": size,
                    "hasVideo": ext in VIDEO_EXT,
                    "root": "media" if root == MEDIA_DIR else "work",
                })
    items.sort(key=lambda i: (i["root"] != "media", i["path"].lower()))
    return {"files": items, "mediaDir": MEDIA_DIR}


@app.get("/api/probe")
def probe(path: str) -> dict:
    info = ffmpeg.probe(resolve(path))
    d = info.to_dict()
    d["path"] = rel(info.path)
    return d


# --------------------------------------------------------------------------
# signals
# --------------------------------------------------------------------------

def _signal(path: str, method: str, stream: int) -> np.ndarray:
    if method == "video":
        return cache.get_or_build(path, "motion", 0, lambda: analysis.video_motion(path))
    return cache.get_or_build(
        path, "novelty", stream, lambda: analysis.audio_novelty(path, stream=stream)
    )


@app.get("/api/waveform")
def waveform(
    path: str,
    stream: int = 0,
    start: float = 0.0,
    end: float = Query(0.0, description="0 = whole file"),
    points: int = Query(1600, ge=50, le=20000),
) -> dict:
    full = resolve(path)
    lo, hi = cache.get_or_build_pair(
        full, "peaks", stream, lambda: analysis.audio_peaks(full, stream=stream, hz=200.0)
    )
    hz = 200.0
    duration = lo.size / hz
    if end <= 0 or end > duration:
        end = duration
    start = max(0.0, min(start, max(0.0, duration - 0.01)))
    if end <= start:
        end = min(duration, start + 0.01)

    i0, i1 = int(start * hz), max(int(start * hz) + 1, int(end * hz))
    seg_lo, seg_hi = lo[i0:i1], hi[i0:i1]
    n = seg_lo.size
    if n == 0:
        return {"start": start, "end": end, "duration": duration, "min": [], "max": []}

    if n <= points:
        out_lo, out_hi = seg_lo, seg_hi
    else:
        bounds = np.linspace(0, n, points + 1).astype(int)
        out_lo = np.minimum.reduceat(seg_lo, bounds[:-1])
        out_hi = np.maximum.reduceat(seg_hi, bounds[:-1])

    return {
        "start": start,
        "end": end,
        "duration": duration,
        "min": [round(float(v), 4) for v in out_lo],
        "max": [round(float(v), 4) for v in out_hi],
    }


# --------------------------------------------------------------------------
# analysis
# --------------------------------------------------------------------------

def _measure(req: AnalyseReq, path_a: str, path_b: str, method: str, job: Optional[Job]) -> dict:
    if job:
        job.stage = f"Signaali lugemine ({method}): A"
        job.progress = 0.05
    sig_a = _signal(path_a, method, req.streamA)
    if job:
        job.stage = f"Signaali lugemine ({method}): B"
        job.progress = 0.45
    sig_b = _signal(path_b, method, req.streamB)
    if job:
        job.stage = f"Ristkorrelatsioon ({method})"
        job.progress = 0.8

    model, measurements = analysis.analyse_pair(
        sig_a, sig_b,
        windows=req.windows,
        window_sec=req.windowSec,
        max_shift_sec=req.maxShiftSec,
        lock_alpha=req.lockAlpha,
    )
    return {
        "method": method,
        "model": model.as_dict(),
        "measurements": [m.__dict__ for m in measurements],
        "signalSeconds": {"a": sig_a.size / analysis.SIGNAL_HZ, "b": sig_b.size / analysis.SIGNAL_HZ},
    }


@app.post("/api/analyse")
def analyse(req: AnalyseReq) -> dict:
    path_a = resolve(req.pathA)
    path_b = resolve(req.pathB)
    info_a = ffmpeg.probe(path_a)
    info_b = ffmpeg.probe(path_b)

    def work(job: Job) -> dict:
        methods = ["video", "audio"] if req.method == "both" else [req.method]
        if req.method == "both" and not (info_a.video_codec and info_b.video_codec):
            methods = ["audio"]

        runs = []
        for i, method in enumerate(methods):
            if job.cancelled():
                break
            res = _measure(req, path_a, path_b, method, job)
            runs.append(res)
            job.progress = (i + 1) / len(methods)

        best = max(runs, key=lambda r: r["model"]["confidence"]) if runs else None
        alpha = best["model"]["alpha"] if best else 1.0

        return {
            "runs": runs,
            "best": best["method"] if best else None,
            "ratioGuess": analysis.classify_ratio(alpha),
            "fpsRatio": analysis.ratio_from_fps(info_a.fps, info_b.fps),
            "infoA": {**info_a.to_dict(), "path": rel(path_a)},
            "infoB": {**info_b.to_dict(), "path": rel(path_b)},
            "knownRatios": [{"name": n, "value": v} for n, v in analysis.KNOWN_RATIOS],
        }

    job = registry.submit("analyse", work)
    return job.to_dict()


@app.post("/api/refit")
def refit(req: RefitReq) -> dict:
    ms = [
        analysis.Measurement(
            t=float(m["t"]), offset=float(m["offset"]),
            score=float(m.get("score", 1.0)), margin=float(m.get("margin", 1.0)),
        )
        for m in req.measurements
    ]
    model = analysis.fit_linear(ms, min_score=req.minScore, tol=req.tol, lock_alpha=req.lockAlpha)
    return {
        "model": model.as_dict(),
        "measurements": [m.__dict__ for m in ms],
        "ratioGuess": analysis.classify_ratio(model.alpha),
    }


@app.post("/api/probe-point")
def probe_point(req: ProbePointReq) -> dict:
    """One extra anchor at a time the user picked, using the cached signals.

    B is de-drifted through the current model first. Correlating the raw
    signals here would hit the same wall the refining passes do: over a window
    of any useful length a few percent of rate mismatch pulls the two ends
    apart by more than a transient is wide, and the peak lands hundreds of
    milliseconds off. Against a warped copy only the residual error is left.
    """
    path_a = resolve(req.pathA)
    path_b = resolve(req.pathB)
    sig_a = _signal(path_a, req.method, req.streamA)
    sig_b = _signal(path_b, req.method, req.streamB)

    hz = analysis.SIGNAL_HZ
    win = int(req.windowSec * hz)
    lag = int(req.maxShiftSec * hz)
    if sig_a.size < win or sig_b.size < win:
        raise HTTPException(400, "Signaal on liiga lühike selle akna jaoks.")

    warped = analysis.warp_signal(sig_b, req.alpha, req.beta, sig_a.size)

    i0 = max(0, min(int(req.t * hz) - win // 2, max(0, sig_a.size - win)))
    b0 = max(0, i0 - lag)
    b1 = min(warped.size, i0 + win + lag)
    if b1 - b0 < win + 2:
        raise HTTPException(400, "Otsinguaken jääb faili B piiridest välja.")

    scores = analysis.normalised_xcorr(sig_a[i0:i0 + win], warped[b0:b1])
    if scores.size == 0:
        raise HTTPException(400, "Korrelatsiooni ei õnnestunud arvutada.")

    k = int(np.argmax(scores))
    guard = max(1, int(0.5 * hz))
    masked = scores.copy()
    masked[max(0, k - guard): k + guard + 1] = -np.inf
    runner = float(np.max(masked)) if np.isfinite(masked).any() else 0.0
    k_ref = analysis._refine_peak(scores, k)

    t_centre = float((i0 + win / 2.0) / hz)
    model_here = (req.alpha - 1.0) * t_centre + req.beta

    # residual r on the warped clock lifts back to B's clock as alpha * r
    def to_absolute(r_samples: float) -> float:
        return model_here + req.alpha * ((b0 + r_samples - i0) / hz)

    step = max(1, scores.size // 1200)
    idx = np.arange(0, scores.size, step)

    return {
        "t": t_centre,
        "offset": to_absolute(k_ref),
        "score": float(scores[k]),
        "margin": float(scores[k] - runner),
        "modelOffset": model_here,
        "curve": {
            "lag": [round(to_absolute(float(i)), 4) for i in idx],
            "score": [round(float(v), 4) for v in scores[idx]],
        },
    }


# --------------------------------------------------------------------------
# preview + export
# --------------------------------------------------------------------------

@app.post("/api/preview")
def preview(req: PreviewReq) -> dict:
    path_a = resolve(req.pathA)
    path_b = resolve(req.pathB)
    name = f"prev-{int(time.time() * 1000)}.mp4"
    out = os.path.join(PREVIEW_DIR, name)

    def work(job: Job) -> dict:
        job.stage = "Eelvaate renderdamine"
        render.render_preview(
            video_a=path_a, donor_b=path_b, out_path=out,
            start=req.start, length=req.length,
            params=req.sync.to_params(),
            donor_stream=req.streamB, keep_stream_a=req.streamA,
            mode=req.mode, height=req.height,
        )
        _prune(PREVIEW_DIR, keep=25)
        return {"url": f"/api/preview-file/{name}", "mode": req.mode,
                "start": req.start, "length": req.length}

    return registry.submit("preview", work).to_dict()


@app.post("/api/export")
def export(req: ExportReq) -> dict:
    path_a = resolve(req.pathA)
    path_b = resolve(req.pathB)
    info_a = ffmpeg.probe(path_a)

    name = req.outName or render.suggest_out_name(path_a, req.donorLang)
    name = re.sub(r"[\\/]+", "_", name)
    out = os.path.join(EXPORT_DIR, name)

    params = req.sync.to_params()

    def work(job: Job) -> dict:
        job.stage = "Ekspordin (video kopeeritakse, heli kodeeritakse uuesti)"

        def on_progress(p: float) -> None:
            job.progress = p

        render.render_export(
            video_a=path_a, donor_b=path_b, out_path=out,
            params=params, duration=info_a.duration,
            donor_stream=req.streamB, keep_original=req.keepOriginal,
            keep_stream_a=req.streamA,
            audio_codec=req.audioCodec, audio_bitrate=req.audioBitrate,
            donor_lang=req.donorLang, original_lang=req.originalLang,
            progress_cb=on_progress,
        )
        return {
            "path": rel(out),
            "abs": out,
            "sizeBytes": os.path.getsize(out) if os.path.exists(out) else 0,
            "command": command_line(req),
        }

    return registry.submit("export", work).to_dict()


@app.post("/api/command")
def command(req: ExportReq) -> dict:
    return {"command": command_line(req)}


def command_line(req: ExportReq) -> str:
    """The equivalent hand-run ffmpeg invocation, for the copy button."""
    params = req.sync.to_params()
    info_a = ffmpeg.probe(resolve(req.pathA))
    graph = render.build_filter(params, label_in=f"1:a:{req.streamB}",
                                label_out="aout", duration=info_a.duration)
    parts = [
        "ffmpeg -y",
        f'-i "{req.pathA}"',
        f'-i "{req.pathB}"',
        f'-filter_complex "{graph}"',
        "-map 0:v:0 -map [aout]",
    ]
    if req.keepOriginal:
        parts.append(f"-map 0:a:{req.streamA}")
    parts += [
        "-c:v copy",
        f"-c:a {req.audioCodec} -b:a {req.audioBitrate}",
        f"-t {info_a.duration:.3f}",
        f'"{req.outName or render.suggest_out_name(req.pathA, req.donorLang)}"',
    ]
    return " ".join(parts)


def _prune(directory: str, keep: int) -> None:
    try:
        files = sorted(
            (os.path.join(directory, f) for f in os.listdir(directory)),
            key=os.path.getmtime, reverse=True,
        )
    except OSError:
        return
    for stale in files[keep:]:
        try:
            os.remove(stale)
        except OSError:
            pass


# --------------------------------------------------------------------------
# jobs + file serving
# --------------------------------------------------------------------------

@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    job = registry.get(job_id)
    if not job:
        raise HTTPException(404, "Tööd ei leitud")
    return job.to_dict()


@app.post("/api/jobs/{job_id}/cancel")
def job_cancel(job_id: str) -> dict:
    return {"cancelled": registry.cancel(job_id)}


@app.post("/api/cache/clear")
def cache_clear() -> dict:
    return {"removed": cache.clear()}


@app.get("/api/preview-file/{name}")
def preview_file(name: str, request: Request):
    safe = os.path.basename(name)
    path = os.path.join(PREVIEW_DIR, safe)
    if not os.path.exists(path):
        raise HTTPException(404, "Eelvaadet ei leitud")
    return _ranged(path, request, "video/mp4")


@app.get("/api/download")
def download(path: str, request: Request):
    full = resolve(path)
    return _ranged(full, request, "application/octet-stream",
                   filename=os.path.basename(full))


def _ranged(path: str, request: Request, media_type: str, filename: str | None = None):
    """Byte-range aware file response so <video> can seek inside previews."""
    size = os.path.getsize(path)
    range_header = request.headers.get("range")
    headers = {"accept-ranges": "bytes"}
    if filename:
        headers["content-disposition"] = f'attachment; filename="{filename}"'

    if not range_header:
        return FileResponse(path, media_type=media_type, headers=headers)

    m = re.match(r"bytes=(\d*)-(\d*)", range_header)
    if not m:
        return FileResponse(path, media_type=media_type, headers=headers)

    start = int(m.group(1)) if m.group(1) else 0
    end = int(m.group(2)) if m.group(2) else size - 1
    end = min(end, size - 1)
    if start > end:
        return Response(status_code=416, headers={"content-range": f"bytes */{size}"})

    with open(path, "rb") as fh:
        fh.seek(start)
        chunk = fh.read(end - start + 1)

    headers.update({
        "content-range": f"bytes {start}-{end}/{size}",
        "content-length": str(len(chunk)),
    })
    return Response(chunk, status_code=206, media_type=media_type, headers=headers)


@app.exception_handler(ffmpeg.FfmpegError)
def ffmpeg_error(_request: Request, exc: ffmpeg.FfmpegError):
    return JSONResponse({"detail": f"ffmpeg: {exc}"}, status_code=500)


if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
