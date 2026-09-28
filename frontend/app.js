import * as api from "./api.js";
import { drawDrift, fmtTime } from "./drift.js";
import { drawWave, drawCorr } from "./waves.js";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

// below this correlation peak an anchor is not trustworthy enough to move the model
const MIN_ANCHOR_SCORE = 0.35;

const state = {
  files: [],
  a: { path: "", info: null, stream: 0 },
  b: { path: "", info: null, stream: 0 },
  measurements: [],
  fitted: { alpha: 1, beta: 0 },
  model: { alpha: 1, beta: 0, pitch: "preserve", gainDb: 0 },
  analysis: null,
  chartMode: "residual",
  view: { start: 0, span: 20 },
  corr: null,
  corrOffset: null,
  hoverX: null,
};

// ─────────────────────────────────────────────────────────── helpers

let toastTimer;
function toast(msg, isError = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.toggle("err", isError);
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), isError ? 8000 : 3500);
}

function progress(sel, job) {
  const el = $(sel);
  if (!job) { el.hidden = true; return; }
  el.hidden = false;
  el.querySelector(".bar i").style.width = `${(job.progress ?? 0) * 100}%`;
  el.querySelector(".stage").textContent = job.stage || "";
}

const bytes = (n) => {
  if (!n) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${u[i]}`;
};

function parseTime(text) {
  const parts = String(text).trim().split(":").map(Number);
  if (parts.some(isNaN) || !parts.length) return 0;
  return parts.reduce((acc, p) => acc * 60 + p, 0);
}

const durationA = () => state.a.info?.duration || 0;

// ─────────────────────────────────────────────────────────── files

async function loadFiles() {
  const { files } = await api.get("/files");
  state.files = files;

  for (const slot of ["a", "b"]) {
    const sel = $(`#file-${slot}`);
    const prev = sel.value;
    sel.innerHTML = '<option value="">— vali fail —</option>';
    for (const f of files) {
      const opt = document.createElement("option");
      opt.value = f.path;
      opt.textContent = `${f.path}  ·  ${bytes(f.sizeBytes)}`;
      sel.append(opt);
    }
    if (prev && files.some((f) => f.path === prev)) sel.value = prev;
  }

  if (!files.length) {
    toast("Kaustas media/ pole ühtki videofaili. Kopeeri failid sinna või muuda docker-compose.yml volume'i.", true);
  }
}

async function selectFile(slot) {
  const path = $(`#file-${slot}`).value;
  const meta = $(`#meta-${slot}`);
  const streamSel = $(`#stream-${slot}`);
  state[slot] = { path, info: null, stream: 0 };
  streamSel.innerHTML = "";

  if (!path) { meta.textContent = "—"; syncEnabled(); return; }

  meta.textContent = "loen…";
  try {
    const info = await api.get("/probe", { path });
    state[slot].info = info;

    const bits = [`<b>${fmtTime(info.duration)}</b>`];
    if (info.width) bits.push(`${info.width}×${info.height}`);
    if (info.fps) bits.push(`<b>${info.fps.toFixed(3)} fps</b>`);
    if (info.videoCodec) bits.push(info.videoCodec);
    bits.push(`${info.audio.length} helirada`);
    meta.innerHTML = bits.join(" · ");

    info.audio.forEach((s, i) => {
      const opt = document.createElement("option");
      opt.value = i;
      opt.textContent = `#${i + 1} ${s.codec}${s.channels ? ` ${s.channels}ch` : ""}` +
        `${s.language ? ` [${s.language}]` : ""}${s.title ? ` — ${s.title}` : ""}`;
      streamSel.append(opt);
    });
    if (!info.audio.length) {
      streamSel.innerHTML = '<option value="0">— heliradasid pole —</option>';
    }
  } catch (err) {
    meta.textContent = `viga: ${err.message}`;
  }

  syncEnabled();
  updateFpsHint();
  suggestOutName();
}

function updateFpsHint() {
  const a = state.a.info, b = state.b.info;
  const el = $("#fps-hint");
  if (!a || !b) { el.textContent = ""; return; }

  const parts = [];
  if (a.fps && b.fps && Math.abs(a.fps - b.fps) > 0.001) {
    const ratio = a.fps / b.fps;
    const perHour = (ratio - 1) * 3600;
    parts.push(
      `Kaadrisagedused erinevad: <b>${a.fps.toFixed(3)}</b> vs <b>${b.fps.toFixed(3)}</b> → ` +
      `eeldatav tempo <b>${ratio.toFixed(6)}×</b>, see on ${Math.abs(perHour).toFixed(1)} s tunnis.`
    );
  }
  const dd = Math.abs(a.duration - b.duration);
  if (dd > 0.5) {
    parts.push(`Kestused erinevad <b>${dd.toFixed(1)} s</b> võrra (${fmtTime(a.duration)} vs ${fmtTime(b.duration)}).`);
  }
  if (!parts.length) parts.push("Kaadrisagedus ja kestus kattuvad — triiv on tõenäoliselt ainult konstantne nihe.");
  el.innerHTML = parts.join(" ");
}

function syncEnabled() {
  $("#btn-analyse").disabled = !(state.a.path && state.b.path);
}

// ─────────────────────────────────────────────────────────── analysis

async function runAnalyse() {
  const body = {
    pathA: state.a.path,
    pathB: state.b.path,
    streamA: +$("#stream-a").value || 0,
    streamB: +$("#stream-b").value || 0,
    method: $("#opt-method").value,
    windows: +$("#opt-windows").value,
    windowSec: +$("#opt-winsec").value,
    maxShiftSec: +$("#opt-maxshift").value,
  };
  state.a.stream = body.streamA;
  state.b.stream = body.streamB;

  $("#btn-analyse").disabled = true;
  $("#analyse-empty").hidden = true;
  try {
    const result = await api.runJob("/analyse", body, (job) => progress("#analyse-progress", job));
    progress("#analyse-progress", null);
    applyAnalysis(result);
  } catch (err) {
    progress("#analyse-progress", null);
    toast(err.message, true);
    $("#analyse-empty").hidden = false;
  } finally {
    $("#btn-analyse").disabled = false;
  }
}

function applyAnalysis(result) {
  state.analysis = result;
  const run = result.runs.find((r) => r.method === result.best) || result.runs[0];
  if (!run) { toast("Analüüs ei andnud tulemust", true); return; }

  state.measurements = run.measurements;
  state.fitted = { alpha: run.model.alpha, beta: run.model.beta };
  state.model.alpha = run.model.alpha;
  state.model.beta = run.model.beta;

  fillRatioPresets(result.knownRatios, result.fpsRatio);
  $("#analyse-result").hidden = false;
  $("#step-fix").hidden = false;
  $("#step-preview").hidden = false;
  $("#step-export").hidden = false;

  state.view = { start: 0, span: Math.min(20, Math.max(4, durationA() / 60)) };

  pushModelToInputs();
  renderVerdict(run, result);
  redrawChart();
  refreshWaves();
  refreshCommand();
}

function fillRatioPresets(known, fpsRatio) {
  const sel = $("#ratio-presets");
  sel.innerHTML = '<option value="">— vali —</option>';
  if (fpsRatio && Math.abs(fpsRatio - 1) > 1e-9) {
    const opt = document.createElement("option");
    opt.value = fpsRatio;
    opt.textContent = `Failide fps-suhtest: ${fpsRatio.toFixed(6)}×`;
    sel.append(opt);
  }
  for (const r of known || []) {
    const opt = document.createElement("option");
    opt.value = r.value;
    opt.textContent = `${r.name} — ${r.value.toFixed(6)}×`;
    sel.append(opt);
  }
}

function renderVerdict(run, result) {
  const m = run.model;
  const dur = durationA();
  const rawStart = m.beta;
  const rawEnd = (m.alpha - 1) * dur + m.beta;
  const ppm = (m.alpha - 1) * 1e6;
  const el = $("#verdict");

  let cls, title, body;

  if (m.confidence < 0.2 || m.inliers < 3) {
    cls = "bad";
    title = "Ei õnnestunud usaldusväärselt mõõta";
    body = `Ainult ${m.inliers}/${m.total} mõõtepunkti klappis. Proovi teist meetodit ` +
      `(pilt vs heli), pikemat akent või suuremat otsinguvahemikku. Kui failid on eri lõikega, ` +
      `ei ole ühest lineaarsest parandusest kasu.`;
  } else if (m.rmsResidualMs > 120) {
    cls = "bad";
    title = "Triiv ei ole ühtlane — failid on tõenäoliselt eri lõikega";
    body = `Jääkviga peale lineaarset parandust on ${m.rmsResidualMs.toFixed(0)} ms (max ` +
      `${m.maxResidualMs.toFixed(0)} ms). Vaata graafikult, kus punktid hüppavad: seal on ` +
      `lõige erinev. Üks tempoparandus neid kokku ei too.`;
  } else if (Math.abs(rawStart) < 0.04 && Math.abs(rawEnd) < 0.04) {
    cls = "good";
    title = "Failid on juba sünkroonis";
    body = `Nihe on kogu pikkuses alla 40 ms. Võid heli otse üle tõsta.`;
  } else {
    cls = m.rmsResidualMs > 45 ? "warn" : "good";
    title = "Lineaarne triiv — parandatav";
    const ratio = result.ratioGuess;
    body =
      `Naiivselt üle tõstes oleks heli alguses ${(rawStart * 1000).toFixed(0)} ms ja lõpus ` +
      `${rawEnd >= 0 ? "+" : ""}${rawEnd.toFixed(2)} s paigast. ` +
      `Tempo ${m.alpha.toFixed(6)}× parandab selle ära; jääkviga ${m.rmsResidualMs.toFixed(0)} ms. ` +
      (ratio ? `See vastab tuntud teisendusele <b>${ratio.name}</b> — soovitan „muuda ka kõrgust“ režiimi.` : "");
  }

  el.className = `verdict ${cls}`;
  el.innerHTML = `<i class="dot"></i><div><h3>${title}</h3><p>${body}</p></div>`;

  $("#readout").innerHTML = [
    box("Nihe algul", `${(rawStart * 1000).toFixed(0)} ms`, "enne parandust"),
    box("Nihe lõpus", `${rawEnd >= 0 ? "+" : ""}${rawEnd.toFixed(2)} s`, `${fmtTime(dur)} juures`),
    box("Tempo", `${m.alpha.toFixed(6)}×`, `${ppm >= 0 ? "+" : ""}${ppm.toFixed(0)} ppm`),
    box("Jääkviga", `${m.rmsResidualMs.toFixed(0)} ms`, `max ${m.maxResidualMs.toFixed(0)} ms`),
    box("Mõõtepunkte", `${m.inliers}/${m.total}`, `meetod: ${run.method === "video" ? "pilt" : "heli"}`),
    box("Kindlus", `${(m.confidence * 100).toFixed(0)} %`, result.runs.length > 1 ? "parim kahest" : ""),
  ].join("");
}

const box = (label, value, note) =>
  `<div class="box"><span>${label}</span><b>${value}</b><small>${note || ""}</small></div>`;

// ─────────────────────────────────────────────────────────── model <-> UI

function pushModelToInputs() {
  $("#in-beta").value = (state.model.beta * 1000).toFixed(0);
  $("#rng-beta").value = Math.max(-5000, Math.min(5000, state.model.beta * 1000));
  $("#in-alpha").value = state.model.alpha.toFixed(6);
  $("#rng-alpha").value = Math.max(0.9, Math.min(1.1, state.model.alpha));
  updateAlphaNote();
}

function updateAlphaNote() {
  const ppm = (state.model.alpha - 1) * 1e6;
  $("#ppm").textContent = `${ppm >= 0 ? "+" : ""}${ppm.toFixed(0)}`;
  $("#perhour").textContent = `${((state.model.alpha - 1) * 3600).toFixed(1)} s/h`;
}

function setModel(patch, { redraw = true } = {}) {
  Object.assign(state.model, patch);
  updateAlphaNote();
  if (redraw) {
    redrawChart();
    refreshWaves();
    refreshCommand();
  }
}

// ─────────────────────────────────────────────────────────── chart

function redrawChart() {
  drawDrift($("#drift-chart"), {
    measurements: state.measurements,
    model: state.model,
    duration: durationA(),
    mode: state.chartMode,
    hover: state.hoverX,
  });
}

// ─────────────────────────────────────────────────────────── waveforms

let waveToken = 0;
let waveTimer;

function refreshWaves() {
  clearTimeout(waveTimer);
  waveTimer = setTimeout(doRefreshWaves, 90);
  drawWaveFrame();
}

function drawWaveFrame(dataA, dataB) {
  const { start, span } = state.view;
  const end = start + span;
  drawWave($("#wave-a"), {
    data: dataA ?? state.dataA,
    start, end,
    colour: "#7f8ca6",
    label: `A · ${state.a.info?.path ?? ""}`,
    cursorX: state.waveCursor,
    empty: "laen…",
  });
  drawWave($("#wave-b"), {
    data: dataB ?? state.dataB,
    start, end,
    colour: "#4dd4e0",
    label: `B parandatuna · α=${state.model.alpha.toFixed(6)} β=${(state.model.beta * 1000).toFixed(0)} ms`,
    cursorX: state.waveCursor,
    empty: "laen…",
  });
  $("#zoom-label").textContent = `${span.toFixed(span < 2 ? 2 : 1)} s aknas`;
  $("#wave-time").textContent = `${fmtTime(start)} – ${fmtTime(end)}`;
  drawCorr($("#corr-curve"), state.corr, state.corrOffset, currentOffsetAt(viewCentre()));
}

const viewCentre = () => state.view.start + state.view.span / 2;
const currentOffsetAt = (t) => (state.model.alpha - 1) * t + state.model.beta;

async function doRefreshWaves() {
  if (!state.a.path || !state.b.path) return;
  const token = ++waveToken;
  const { start, span } = state.view;
  const end = start + span;

  const bStart = state.model.alpha * start + state.model.beta;
  const bEnd = state.model.alpha * end + state.model.beta;
  const points = Math.max(200, Math.min(3000, Math.round($("#wave-a").clientWidth)));

  try {
    const [dataA, dataB] = await Promise.all([
      api.get("/waveform", { path: state.a.path, stream: state.a.stream, start, end, points }),
      api.get("/waveform", {
        path: state.b.path, stream: state.b.stream,
        start: Math.max(0, bStart), end: Math.max(0.01, bEnd), points,
      }),
    ]);
    if (token !== waveToken) return;
    state.dataA = dataA;
    state.dataB = dataB;
    drawWaveFrame(dataA, dataB);
  } catch (err) {
    if (token === waveToken) toast(err.message, true);
  }
}

function setView(start, span) {
  const dur = durationA() || 1;
  span = Math.max(0.25, Math.min(span, dur));
  start = Math.max(0, Math.min(start, dur - span));
  state.view = { start, span };
  refreshWaves();
}

// ─────────────────────────────────────────────────────────── probe

async function measureHere() {
  const t = viewCentre();
  const btn = $("#btn-measure-here");
  btn.disabled = true;
  try {
    const res = await api.post("/probe-point", {
      pathA: state.a.path, pathB: state.b.path,
      streamA: state.a.stream, streamB: state.b.stream,
      method: state.analysis?.best === "audio" ? "audio" : "video",
      t,
      windowSec: Math.max(4, Math.min(30, state.view.span * 1.5)),
      maxShiftSec: Math.max(1, Math.min(10, state.view.span)),
      alpha: state.model.alpha,
      beta: state.model.beta,
    });

    state.corr = res.curve;
    state.corrOffset = res.offset;

    // A weak peak here is not a measurement, it is a guess -- and applying it
    // silently would drag a good model off by half a second. Draw the curve so
    // the ambiguity is visible, but leave the model alone.
    if (res.score < MIN_ANCHOR_SCORE) {
      drawWaveFrame();
      toast(
        `Nõrk vaste siin (r=${res.score.toFixed(2)}) — ei joondanud. ` +
        `Proovi kohta, kus on selge löök või stseenivahetus, või suurenda akent.`,
        true
      );
      return;
    }

    const known = state.measurements.some((m) => Math.abs(m.t - res.t) < 0.5);
    if (!known) state.measurements = [...state.measurements, { ...res, used: true }];

    // keep alpha, move the line through this anchor
    setModel({ beta: res.offset - (state.model.alpha - 1) * res.t });
    pushModelToInputs();

    const moved = (res.offset - res.modelOffset) * 1000;
    toast(
      `Joondatud ${fmtTime(res.t)} juures: nihutasin ${moved >= 0 ? "+" : ""}${moved.toFixed(0)} ms ` +
      `(r=${res.score.toFixed(2)})`
    );
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false;
  }
}

async function refit() {
  if (!state.measurements.length) return;
  try {
    const res = await api.post("/refit", {
      measurements: state.measurements,
      lockAlpha: $("#lock-alpha").checked ? state.model.alpha : null,
    });
    state.measurements = res.measurements;
    state.fitted = { alpha: res.model.alpha, beta: res.model.beta };
    setModel({ alpha: res.model.alpha, beta: res.model.beta }, { redraw: false });
    pushModelToInputs();
    redrawChart();
    refreshWaves();
    refreshCommand();
    toast(`Sobitatud uuesti: ${res.model.inliers}/${res.model.total} punkti, jääk ${res.model.rmsResidualMs.toFixed(0)} ms`);
  } catch (err) {
    toast(err.message, true);
  }
}

// ─────────────────────────────────────────────────────────── preview / export

function syncBody() {
  return {
    alpha: state.model.alpha,
    beta: state.model.beta,
    pitch: $("#in-pitch").value,
    gainDb: +$("#in-gain").value || 0,
  };
}

async function renderPreview() {
  const btn = $("#btn-preview");
  btn.disabled = true;
  try {
    const res = await api.runJob("/preview", {
      pathA: state.a.path, pathB: state.b.path,
      streamA: state.a.stream, streamB: state.b.stream,
      start: parseTime($("#prev-start").value),
      length: +$("#prev-len").value || 12,
      mode: $("#prev-mode").value,
      sync: syncBody(),
    }, (job) => progress("#preview-progress", job));
    progress("#preview-progress", null);
    const player = $("#player");
    player.src = res.url;
    player.play().catch(() => {});
  } catch (err) {
    progress("#preview-progress", null);
    toast(err.message, true);
  } finally {
    btn.disabled = false;
  }
}

function exportBody() {
  return {
    pathA: state.a.path, pathB: state.b.path,
    streamA: state.a.stream, streamB: state.b.stream,
    outName: $("#out-name").value || null,
    keepOriginal: $("#out-keep").checked,
    audioCodec: $("#out-codec").value,
    audioBitrate: $("#out-bitrate").value,
    donorLang: $("#out-lang").value || "und",
    sync: syncBody(),
  };
}

async function runExport() {
  const btn = $("#btn-export");
  btn.disabled = true;
  $("#export-result").hidden = true;
  try {
    const res = await api.runJob("/export", exportBody(), (job) => progress("#export-progress", job));
    progress("#export-progress", null);
    const el = $("#export-result");
    el.hidden = false;
    el.innerHTML =
      `Valmis: <code>${res.path}</code> · ${bytes(res.sizeBytes)} ` +
      `<a href="/api/download?path=${encodeURIComponent(res.path)}">laadi alla</a>` +
      `<br><small>Konteineris: <code>${res.abs}</code> — hostis kaustas <code>work/exports/</code>.</small>`;
    toast("Eksport valmis");
  } catch (err) {
    progress("#export-progress", null);
    toast(err.message, true);
  } finally {
    btn.disabled = false;
  }
}

let cmdTimer;
function refreshCommand() {
  clearTimeout(cmdTimer);
  cmdTimer = setTimeout(async () => {
    if (!state.a.path || !state.b.path) return;
    try {
      const res = await api.post("/command", exportBody());
      $("#cmd-text").textContent = res.command;
    } catch { /* not critical */ }
  }, 300);
}

function suggestOutName() {
  if (!state.a.info || $("#out-name").value) return;
  const base = state.a.path.split("/").pop().replace(/\.[^.]+$/, "");
  const ext = state.a.path.match(/\.(mkv|mp4|mov|webm)$/i)?.[0] || ".mkv";
  $("#out-name").value = `${base}.synced${ext}`;
}

// ─────────────────────────────────────────────────────────── wiring

function bindWaveInteraction() {
  for (const canvas of [$("#wave-a"), $("#wave-b")]) {
    canvas.addEventListener("wheel", (ev) => {
      ev.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const frac = (ev.clientX - rect.left) / rect.width;
      if (ev.ctrlKey || ev.metaKey) {
        const factor = ev.deltaY > 0 ? 1.35 : 1 / 1.35;
        const anchor = state.view.start + frac * state.view.span;
        const span = state.view.span * factor;
        setView(anchor - frac * span, span);
      } else {
        setView(state.view.start + (ev.deltaY > 0 ? 0.25 : -0.25) * state.view.span, state.view.span);
      }
    }, { passive: false });

    canvas.addEventListener("mousemove", (ev) => {
      const rect = canvas.getBoundingClientRect();
      state.waveCursor = ev.clientX - rect.left;
      if (!drag) drawWaveFrame();
    });
    canvas.addEventListener("mouseleave", () => { state.waveCursor = null; drawWaveFrame(); });
  }

  // dragging the donor panel changes beta: B(α·t + β) must keep showing the
  // same content at the new screen position, so β -= α·Δt
  let drag = null;
  const bCanvas = $("#wave-b");
  bCanvas.addEventListener("pointerdown", (ev) => {
    bCanvas.setPointerCapture(ev.pointerId);
    drag = { x: ev.clientX, beta0: state.model.beta };
  });
  bCanvas.addEventListener("pointermove", (ev) => {
    if (!drag) return;
    const pxPerSec = bCanvas.clientWidth / state.view.span;
    const dt = (ev.clientX - drag.x) / pxPerSec;
    setModel({ beta: drag.beta0 - state.model.alpha * dt }, { redraw: false });
    pushModelToInputs();
    drawWaveFrame();
    redrawChart();
  });
  const endDrag = () => {
    if (!drag) return;
    drag = null;
    refreshWaves();
    refreshCommand();
  };
  bCanvas.addEventListener("pointerup", endDrag);
  bCanvas.addEventListener("pointercancel", endDrag);
}

function bindChartInteraction() {
  const canvas = $("#drift-chart");
  canvas.addEventListener("mousemove", (ev) => {
    state.hoverX = ev.clientX - canvas.getBoundingClientRect().left;
    redrawChart();
  });
  canvas.addEventListener("mouseleave", () => { state.hoverX = null; redrawChart(); });
  canvas.addEventListener("click", (ev) => {
    const rect = canvas.getBoundingClientRect();
    const frac = (ev.clientX - rect.left - 62) / (rect.width - 78);
    const t = Math.max(0, Math.min(1, frac)) * Math.max(durationA(), 1);
    setView(t - state.view.span / 2, state.view.span);
    $("#prev-start").value = fmtTime(Math.max(0, t - 2));
  });
}

function bindControls() {
  $("#btn-reload-files").onclick = () => loadFiles().then(() => toast("Nimekiri värskendatud"));
  $("#file-a").onchange = () => selectFile("a");
  $("#file-b").onchange = () => selectFile("b");
  $("#stream-a").onchange = () => { state.a.stream = +$("#stream-a").value || 0; refreshWaves(); };
  $("#stream-b").onchange = () => { state.b.stream = +$("#stream-b").value || 0; refreshWaves(); };
  $("#btn-analyse").onclick = runAnalyse;

  $("#in-beta").oninput = () => {
    const ms = +$("#in-beta").value || 0;
    $("#rng-beta").value = Math.max(-5000, Math.min(5000, ms));
    setModel({ beta: ms / 1000 });
  };
  $("#rng-beta").oninput = () => {
    const ms = +$("#rng-beta").value;
    $("#in-beta").value = ms.toFixed(0);
    setModel({ beta: ms / 1000 });
  };
  $("#in-alpha").oninput = () => {
    const a = +$("#in-alpha").value || 1;
    $("#rng-alpha").value = Math.max(0.9, Math.min(1.1, a));
    setModel({ alpha: a });
  };
  $("#rng-alpha").oninput = () => {
    const a = +$("#rng-alpha").value;
    $("#in-alpha").value = a.toFixed(6);
    setModel({ alpha: a });
  };
  $("#ratio-presets").onchange = (ev) => {
    if (!ev.target.value) return;
    const alpha = +ev.target.value;
    $("#in-alpha").value = alpha.toFixed(6);
    $("#rng-alpha").value = Math.max(0.9, Math.min(1.1, alpha));
    setModel({ alpha });
    if (state.measurements.length) refit();
  };
  $("#lock-alpha").onchange = () => { if (state.measurements.length) refit(); };
  $("#in-pitch").onchange = refreshCommand;
  $("#in-gain").oninput = refreshCommand;

  $("#btn-reset-model").onclick = () => {
    setModel({ alpha: state.fitted.alpha, beta: state.fitted.beta }, { redraw: false });
    pushModelToInputs();
    redrawChart();
    refreshWaves();
    refreshCommand();
  };

  $$("[data-jump]").forEach((btn) => {
    btn.onclick = () => {
      const frac = +btn.dataset.jump;
      setView(durationA() * frac - state.view.span / 2, state.view.span);
      $("#prev-start").value = fmtTime(Math.max(0, durationA() * frac - 2));
    };
  });
  $("#btn-zoom-in").onclick = () => setView(viewCentre() - state.view.span / 2.7, state.view.span / 1.7);
  $("#btn-zoom-out").onclick = () => setView(viewCentre() - state.view.span * 0.85, state.view.span * 1.7);
  $("#btn-measure-here").onclick = measureHere;

  $("#btn-preview").onclick = renderPreview;
  $("#btn-export").onclick = runExport;
  $("#out-codec").onchange = refreshCommand;
  $("#out-bitrate").oninput = refreshCommand;
  $("#out-keep").onchange = refreshCommand;
  $("#btn-copy-cmd").onclick = () => {
    navigator.clipboard.writeText($("#cmd-text").textContent).then(
      () => toast("Kopeeritud"),
      () => toast("Kopeerimine ebaõnnestus", true)
    );
  };

  window.addEventListener("resize", () => { redrawChart(); drawWaveFrame(); });
}

function bindChartModeToggle() {
  const wrap = document.querySelector(".chart-legend");
  const btn = document.createElement("button");
  btn.className = "ghost sm";
  btn.style.marginLeft = "auto";
  btn.textContent = "Näita toorest triivi";
  btn.onclick = () => {
    state.chartMode = state.chartMode === "residual" ? "raw" : "residual";
    btn.textContent = state.chartMode === "raw" ? "Näita jääkviga" : "Näita toorest triivi";
    const raw = state.chartMode === "raw";
    wrap.querySelectorAll("span").forEach((s, i) => { if (i < 3) s.hidden = raw; });
    redrawChart();
  };
  wrap.append(btn);
}

async function checkHealth() {
  const el = $("#health");
  try {
    const h = await api.get("/health");
    if (h.ok) {
      el.textContent = `ffmpeg OK · ${h.mediaDir}`;
      el.className = "pill pill-ok";
    } else {
      el.textContent = "ffmpeg puudub";
      el.className = "pill pill-bad";
      toast(`ffmpeg ei vasta: ${h.ffmpegError}`, true);
    }
  } catch (err) {
    el.textContent = "server ei vasta";
    el.className = "pill pill-bad";
  }
}

bindControls();
bindWaveInteraction();
bindChartInteraction();
bindChartModeToggle();
checkHealth();
loadFiles();
