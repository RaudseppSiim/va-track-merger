# Audio Track Merger

Takes the audio track from one video and puts it onto another version of the
same video — and shows beforehand whether the audio drifts away from the picture
and by how much.

Why a plain `ffmpeg -map` usually doesn't work: two releases of the same film
are often at different frame rates. A 25 fps PAL version is **4.1% shorter**
than a 23.976 fps version, which adds up to **147 seconds per hour** — halfway
through the film the audio is already a minute off. `-map` doesn't fix this,
because it doesn't change the duration.

---

## Quick start

```bash
docker compose up
```

Open <http://localhost:5174>. Videos go in the `media/` folder, finished files
end up in `work/exports/`.

If your files live elsewhere, change the volume in `docker-compose.yml`:

```yaml
    volumes:
      - "D:/Movies:/media:ro"
      - ./work:/work
```

### Native window

Build the package — **no Node or npm needed on the host**, the container does
everything:

```bash
docker compose --profile desktop run --rm desktop-build
```

Then run `dist/AudioTrackMerger/Audio-Track-Merger.exe`.

The window finds the project folder on its own, starts the container if it
isn't running yet, waits until it's ready and opens the UI. If *it* started the
container, it stops it when the window is closed; a container that was already
running is left alone.

The build doesn't need Node because the Electron app *is* simply
`resources/app/` next to the prebuilt binary — no compiler, no bundler. The
image downloads the official Electron archive and drops two files into it.

For another platform, change `ELECTRON_PLATFORM` in `docker-compose.yml`
(`linux-x64`, `darwin-arm64`).

Electron itself doesn't run inside the container — that would need X11/VNC
forwarding and would be worse than the browser. So: Docker does the work, the
window lives on the host.

<details>
<summary>Development straight from source (needs Node 18+)</summary>

```bash
cd desktop && npm install && npm start
```
</details>

---

## How to use it

**1. Files.** `A` is the video whose **picture we keep**. `B` is the file we
**take the audio from** — it can also be an audio-only file (`.mka`, `.ac3`,
`.flac`…). The frame rate difference is shown right away.

**2. Measure the offset.** The tool reads a signal from both files and measures
at ~24 points across the film how far the audio is from the picture. Two
methods:

| Method | What it correlates | When |
|---|---|---|
| **picture** | inter-frame motion energy | same cut, any languages — completely language-independent |
| **audio** | multi-band spectral flux | when the picture differs in quality or B has no picture; relies on the music/effects layer |

By default both run and the more confident one is chosen.

**3. The chart is the answer.** Two views, the "Show raw drift" button switches
between them:

- **Residual error** — what remains *after* the correction. Inside the green
  band (±40 ms) = imperceptible. All points in the green = a linear correction
  covers it completely.
- **Raw drift** — what would happen with a naive transfer. **A straight sloped
  line** = frame rate difference, correctable. **A staircase or jumps** = the
  files have different cuts (different intro, ad breaks) and a single tempo
  correction won't bring them together.

**4. Correction.** The measured values are already filled in. Manually:

- **Offset** — a constant delay in milliseconds.
- **Tempo** — the speed multiplier. The dropdown has the well-known ratios
  (PAL 25↔23.976, NTSC 24↔23.976 etc.); if the measured value matches one of
  them, the tool says so itself.
- **Pitch** — `atempo` keeps the pitch in place, `asetrate` changes speed and
  pitch together. **For PAL correction `asetrate` is the right one**: the PAL
  speed-up raised the pitch by 4% back in the day, and `asetrate` takes that
  back.
- **Dragging** the donor track with the mouse changes the offset; "Measure here"
  measures in the current view and aligns. On a weak match
  (correlation below 0.35) it does *not* align but says so — a wrong anchor
  would break a good model.

The waveforms are stacked and the lower one is **already corrected** — when the
transients line up, it's in sync.

**5. Listen.** Renders a short excerpt. By default "original left / new right"
— with headphones you hear an offset immediately, much faster than looking.

**6. Export.** The video is copied unchanged (`-c:v copy`), only the audio is
encoded. Optionally the original audio stays as a second track. Under
"Corresponding ffmpeg command" is the same thing for running by hand.

---

## How the measurement works

The model is linear: `tB = α·tA + β`, where `α` is also the `atempo`
multiplier.

1. **Signal.** From the picture: 32×18 grayscale frames decimated to 12.5 fps,
   inter-frame change. From the audio: 10-band spectral flux on a 50 Hz grid.
   Both are cached (`work/cache/`), so changing settings is instant.

2. **Coarse pass.** Short windows on a **heavily smoothed** signal. The
   smoothing is essential: the ends of a `W`-second window drift apart by
   `W·(1−α)` — with PAL that's 0.32 s in an 8-second window. Features narrower
   than that never meet at any lag and the correlation peak disappears.

3. **Fitting with RANSAC.** Least squares won't do: on repetitive footage the
   correlation yields a minority of confident matches that are seconds off, and
   those pull a median-based fit off the line. The consensus is found by voting
   over all point pairs.

4. **Refinement passes.** B is re-timed with the found model and measured
   again — now the residual stretch is negligible, so long sharp windows work
   and the resolution is in milliseconds. If a refinement pass comes back with
   *less* consensus than before, it has locked onto a side peak and the result
   is discarded.

`confidence` carries the internal consistency (how many points agreed, how
spread out they are, how large the residual is). When it's low, the tool says
"Could not measure reliably" — instead of handing you a nice-looking wrong
answer.

---

## Verification

Test clips whose drift is **known exactly**:

```bash
python tools/make_test_clips.py media
python tools/selftest.py media/test_A_23.976fps.mkv media/test_B_25fps.mkv 0.959041 -1.52
```

Expectation: both methods land within ~20 ms. (The test clips' own uncertainty
is ~21 ms — half a frame at 23.976 fps — so nothing finer can be measured with
them.)

And check every real export — it measures the finished file's two audio tracks
against each other:

```bash
python tools/verify_export.py work/exports/my_file.mkv
```

It should report in sync, with an offset below 40 ms at both ends.

---

## When it doesn't work

**"Drift is not uniform — the files probably have different cuts."** Look at
the raw drift:
where the points jump, the cut differs. A linear correction won't help; the
current tool corrects one straight line at a time. A working approach: export
the film in pieces, with its own offset for each piece.

**The measurement finds nothing.** Try the other method. If the pictures are
from different releases (different crop, logos, restored), use audio. If the
audio layers are completely different (different music), use picture. Raise
"Max search" if the offset might be over 30 s.

**The audio is in sync but the pitch is off.** Switch `atempo` ↔ `asetrate`.

---

## Directories

```
backend/app/
  analysis.py   signals, correlation, RANSAC, iterative refinement
  render.py     SyncModel -> ffmpeg filter chains, preview, export
  main.py       HTTP API
  cache.py      on-disk cache for decoded signals
  jobs.py       background jobs
frontend/       no build step: HTML + ES modules + canvas
tools/          test clip generator, self-test, export check
desktop/        Electron shell + Dockerfile that assembles the package
dist/           built desktop package (not under version control)
```

Local development without Docker: `./dev.sh` (needs a `.venv` and ffmpeg on
PATH or in the `FFMPEG_BIN`/`FFPROBE_BIN` environment variables).
