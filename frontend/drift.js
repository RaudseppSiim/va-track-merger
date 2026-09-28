/**
 * The drift chart: the one picture that answers "does it drift, and by how much".
 *
 * mode "residual" — Y is the error that would remain after the current
 *   correction, in ms, with perceptual tolerance bands. Points inside the
 *   green band mean the linear model explains the drift completely.
 *
 * mode "raw" — Y is the uncorrected offset, so you can see what a plain
 *   `ffmpeg -map` would have produced. A straight slope here is a framerate
 *   mismatch; a staircase means the two files are different edits.
 */

const PAD = { top: 16, right: 16, bottom: 30, left: 62 };

export const fmtTime = (s) => {
  if (!isFinite(s)) return "—";
  const neg = s < 0;
  s = Math.abs(s);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const core = h
    ? `${h}:${String(m).padStart(2, "0")}:${sec.toFixed(0).padStart(2, "0")}`
    : `${m}:${sec.toFixed(1).padStart(4, "0")}`;
  return (neg ? "−" : "") + core;
};

const fmtOffset = (sec) =>
  Math.abs(sec) < 1 ? `${(sec * 1000).toFixed(0)} ms` : `${sec.toFixed(3)} s`;

function crisp(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth;
  const h = canvas.getAttribute("height") * 1;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  return { ctx, w, h };
}

function niceStep(range, targetTicks) {
  const raw = range / targetTicks;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  for (const mult of [1, 2, 2.5, 5, 10]) {
    if (mag * mult >= raw) return mag * mult;
  }
  return mag * 10;
}

export function drawDrift(canvas, { measurements = [], model, duration, mode = "residual", hover = null }) {
  const { ctx, w, h } = crisp(canvas);
  const plotW = w - PAD.left - PAD.right;
  const plotH = h - PAD.top - PAD.bottom;
  if (plotW <= 10 || plotH <= 10) return null;

  const alpha = model?.alpha ?? 1;
  const beta = model?.beta ?? 0;
  const modelAt = (t) => (alpha - 1) * t + beta;

  const tMax = Math.max(duration || 1, ...measurements.map((m) => m.t), 1);
  const value = (m) => (mode === "raw" ? m.offset : m.offset - modelAt(m.t));

  // ── y range ─────────────────────────────────────────────
  let yMax;
  if (mode === "raw") {
    const ends = [modelAt(0), modelAt(tMax)];
    yMax = Math.max(0.05, ...measurements.map((m) => Math.abs(m.offset)), ...ends.map(Math.abs)) * 1.15;
  } else {
    const used = measurements.filter((m) => m.used);
    const spread = Math.max(0.02, ...(used.length ? used : measurements).map((m) => Math.abs(value(m))));
    yMax = Math.max(0.15, spread * 1.3);
  }

  const X = (t) => PAD.left + (t / tMax) * plotW;
  const Y = (v) => PAD.top + plotH / 2 - (v / yMax) * (plotH / 2);

  // ── tolerance bands (residual mode only) ────────────────
  if (mode === "residual") {
    const band = (limit, colour) => {
      const top = Y(Math.min(limit, yMax));
      const bottom = Y(-Math.min(limit, yMax));
      ctx.fillStyle = colour;
      ctx.fillRect(PAD.left, top, plotW, bottom - top);
    };
    band(yMax, "rgba(224,92,92,.10)");
    band(0.1, "rgba(224,180,71,.12)");
    band(0.04, "rgba(70,194,106,.15)");
  }

  // ── grid ────────────────────────────────────────────────
  ctx.strokeStyle = "#2a2f3b";
  ctx.fillStyle = "#8c94a8";
  ctx.font = "11px ui-monospace, Consolas, monospace";
  ctx.lineWidth = 1;

  const yStep = niceStep(yMax * 2, 6);
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";
  for (let v = -Math.ceil(yMax / yStep) * yStep; v <= yMax + 1e-9; v += yStep) {
    if (Math.abs(v) > yMax) continue;
    const y = Y(v);
    ctx.globalAlpha = Math.abs(v) < 1e-9 ? 0.9 : 0.35;
    ctx.beginPath();
    ctx.moveTo(PAD.left, y);
    ctx.lineTo(w - PAD.right, y);
    ctx.stroke();
    ctx.globalAlpha = 1;
    ctx.fillText(fmtOffset(v), PAD.left - 8, y);
  }

  const tStep = niceStep(tMax, 7);
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  for (let t = 0; t <= tMax + 1e-9; t += tStep) {
    const x = X(t);
    ctx.globalAlpha = 0.28;
    ctx.beginPath();
    ctx.moveTo(x, PAD.top);
    ctx.lineTo(x, PAD.top + plotH);
    ctx.stroke();
    ctx.globalAlpha = 1;
    ctx.fillText(fmtTime(t), x, PAD.top + plotH + 7);
  }

  // ── fitted model ────────────────────────────────────────
  ctx.strokeStyle = "#4dd4e0";
  ctx.lineWidth = 1.8;
  ctx.beginPath();
  if (mode === "raw") {
    ctx.moveTo(X(0), Y(modelAt(0)));
    ctx.lineTo(X(tMax), Y(modelAt(tMax)));
  } else {
    ctx.moveTo(X(0), Y(0));
    ctx.lineTo(X(tMax), Y(0));
  }
  ctx.stroke();

  // ── measurements ────────────────────────────────────────
  const points = [];
  for (const m of measurements) {
    const v = value(m);
    const x = X(m.t);
    const y = Y(Math.max(-yMax, Math.min(yMax, v)));
    const clipped = Math.abs(v) > yMax;
    points.push({ x, y, m, v });

    ctx.beginPath();
    ctx.arc(x, y, m.used ? 4.2 : 3, 0, Math.PI * 2);
    if (m.used) {
      const conf = Math.max(0.25, Math.min(1, m.score));
      ctx.fillStyle = `rgba(77,212,224,${conf})`;
    } else {
      ctx.fillStyle = "#4a5163";
    }
    ctx.fill();
    if (clipped) {
      ctx.strokeStyle = "#e05c5c";
      ctx.lineWidth = 1.4;
      ctx.stroke();
    }
  }

  // ── hover marker ────────────────────────────────────────
  if (hover != null) {
    const nearest = points.reduce(
      (best, p) => (Math.abs(p.x - hover) < Math.abs(best.x - hover) ? p : best),
      points[0]
    );
    if (nearest && Math.abs(nearest.x - hover) < 26) {
      ctx.strokeStyle = "#dfe3ec";
      ctx.lineWidth = 1;
      ctx.globalAlpha = 0.5;
      ctx.beginPath();
      ctx.moveTo(nearest.x, PAD.top);
      ctx.lineTo(nearest.x, PAD.top + plotH);
      ctx.stroke();
      ctx.globalAlpha = 1;

      const label = `${fmtTime(nearest.m.t)}   ${fmtOffset(nearest.v)}   r=${nearest.m.score.toFixed(2)}`;
      ctx.font = "11px ui-monospace, Consolas, monospace";
      const tw = ctx.measureText(label).width + 14;
      const bx = Math.min(Math.max(nearest.x - tw / 2, PAD.left), w - PAD.right - tw);
      ctx.fillStyle = "rgba(20,23,30,.95)";
      ctx.strokeStyle = "#3a4252";
      ctx.beginPath();
      ctx.roundRect(bx, PAD.top + 4, tw, 22, 5);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = "#dfe3ec";
      ctx.textAlign = "left";
      ctx.textBaseline = "middle";
      ctx.fillText(label, bx + 7, PAD.top + 15);
    }
  }

  // ── axis captions ───────────────────────────────────────
  ctx.fillStyle = "#6f7891";
  ctx.font = "10px system-ui, sans-serif";
  ctx.textAlign = "left";
  ctx.textBaseline = "top";
  ctx.fillText(
    mode === "raw" ? "raw offset (audio lags ▲ / leads ▼)" : "residual error after correction",
    PAD.left + 4,
    4
  );

  return { X, Y, tMax, yMax };
}
