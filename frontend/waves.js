/**
 * Waveform panels. The donor panel is fetched over the *mapped* time range
 * (alpha·t + beta), so it is already time-corrected by the current model --
 * when transients line up vertically between the two panels, the fix is right.
 */
import { fmtTime } from "./drift.js";

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

function gridStep(span) {
  for (const s of [0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600]) {
    if (span / s <= 12) return s;
  }
  return 900;
}

export function drawWave(canvas, { data, start, end, colour, label, cursorX = null, empty = "" }) {
  const { ctx, w, h } = crisp(canvas);
  const mid = h / 2;
  const span = Math.max(end - start, 1e-6);

  ctx.fillStyle = "#13161d";
  ctx.fillRect(0, 0, w, h);

  // time grid on A's timeline -- identical on both panels by construction
  const step = gridStep(span);
  ctx.strokeStyle = "#252a35";
  ctx.lineWidth = 1;
  ctx.font = "10px ui-monospace, Consolas, monospace";
  ctx.fillStyle = "#5e6679";
  ctx.textAlign = "left";
  ctx.textBaseline = "top";
  for (let t = Math.ceil(start / step) * step; t <= end; t += step) {
    const x = Math.round(((t - start) / span) * w) + 0.5;
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, h);
    ctx.stroke();
    ctx.fillText(fmtTime(t), x + 3, 3);
  }

  ctx.strokeStyle = "#2f3542";
  ctx.beginPath();
  ctx.moveTo(0, mid + 0.5);
  ctx.lineTo(w, mid + 0.5);
  ctx.stroke();

  if (!data || !data.max?.length) {
    ctx.fillStyle = "#5e6679";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.font = "12px system-ui, sans-serif";
    ctx.fillText(empty || "…", w / 2, mid);
    return;
  }

  const n = data.max.length;
  const gain = mid * 0.92;
  ctx.strokeStyle = colour;
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let i = 0; i < n; i++) {
    const x = Math.round((i / n) * w) + 0.5;
    const top = mid - Math.max(-1, Math.min(1, data.max[i])) * gain;
    const bot = mid - Math.max(-1, Math.min(1, data.min[i])) * gain;
    ctx.moveTo(x, top);
    ctx.lineTo(x, Math.max(bot, top + 0.8));
  }
  ctx.stroke();

  if (cursorX != null) {
    ctx.strokeStyle = "#ffffff";
    ctx.globalAlpha = 0.55;
    ctx.beginPath();
    ctx.moveTo(Math.round(cursorX) + 0.5, 0);
    ctx.lineTo(Math.round(cursorX) + 0.5, h);
    ctx.stroke();
    ctx.globalAlpha = 1;
  }

  if (label) {
    ctx.font = "11px system-ui, sans-serif";
    const tw = ctx.measureText(label).width + 12;
    ctx.fillStyle = "rgba(15,17,22,.78)";
    ctx.fillRect(0, h - 19, tw, 19);
    ctx.fillStyle = "#9aa3b7";
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    ctx.fillText(label, 6, h - 9);
  }
}

/** Cross-correlation score vs lag, for the "Measure here" probe. */
export function drawCorr(canvas, curve, chosenOffset, currentOffset) {
  const { ctx, w, h } = crisp(canvas);
  ctx.fillStyle = "#13161d";
  ctx.fillRect(0, 0, w, h);

  if (!curve || !curve.lag?.length) {
    ctx.fillStyle = "#5e6679";
    ctx.font = "11px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText("Correlation curve — press “Measure here”", w / 2, h / 2);
    return;
  }

  const lags = curve.lag;
  const scores = curve.score;
  const lo = lags[0];
  const hi = lags[lags.length - 1];
  const span = Math.max(hi - lo, 1e-6);
  const X = (l) => ((l - lo) / span) * w;

  let peak = 0;
  for (const s of scores) peak = Math.max(peak, Math.abs(s));
  peak = Math.max(peak, 0.1);
  const Y = (s) => h - 14 - (s / peak) * (h - 22);

  ctx.strokeStyle = "#2f3542";
  ctx.beginPath();
  ctx.moveTo(0, Y(0) + 0.5);
  ctx.lineTo(w, Y(0) + 0.5);
  ctx.stroke();

  ctx.strokeStyle = "#7c86a0";
  ctx.lineWidth = 1.2;
  ctx.beginPath();
  scores.forEach((s, i) => (i ? ctx.lineTo(X(lags[i]), Y(s)) : ctx.moveTo(X(lags[i]), Y(s))));
  ctx.stroke();

  const mark = (value, colour, text) => {
    if (value == null || value < lo || value > hi) return;
    const x = X(value);
    ctx.strokeStyle = colour;
    ctx.lineWidth = 1.4;
    ctx.setLineDash([3, 3]);
    ctx.beginPath();
    ctx.moveTo(x, 2);
    ctx.lineTo(x, h - 12);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = colour;
    ctx.font = "10px ui-monospace, Consolas, monospace";
    ctx.textAlign = x > w - 70 ? "right" : "left";
    ctx.textBaseline = "top";
    ctx.fillText(text, x + (x > w - 70 ? -4 : 4), 3);
  };

  mark(currentOffset, "#4dd4e0", `current ${(currentOffset * 1000).toFixed(0)} ms`);
  mark(chosenOffset, "#e0b447", `peak ${(chosenOffset * 1000).toFixed(0)} ms`);

  ctx.fillStyle = "#5e6679";
  ctx.font = "10px ui-monospace, Consolas, monospace";
  ctx.textAlign = "left";
  ctx.textBaseline = "bottom";
  ctx.fillText(`${(lo * 1000).toFixed(0)} ms`, 3, h - 1);
  ctx.textAlign = "right";
  ctx.fillText(`${(hi * 1000).toFixed(0)} ms`, w - 3, h - 1);
}
