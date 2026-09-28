// Thin Electron shell around the containerised app.
//
// Electron itself cannot sensibly run inside the container -- it needs a display
// server, which on Windows means X11 or VNC forwarding and a worse experience
// than the browser. So the split is: Docker owns ffmpeg and all the processing,
// this process owns the window. It brings the container up if it is not
// already answering, waits for the health endpoint, and loads the UI.

const { app, BrowserWindow, shell, dialog, Menu } = require("electron");
const { spawn, spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const URL = process.env.MERGER_URL || "http://127.0.0.1:5174";
const START_TIMEOUT_MS = 180_000;

/**
 * Locate the checkout that holds docker-compose.yml.
 *
 * Running from source this is simply the parent of desktop/, but in the
 * packaged build the app lives at <bundle>/resources/app and the exe sits two
 * levels above that, so a fixed relative path would point inside the bundle.
 * Walking up from both anchors covers every layout without configuration.
 */
function findProjectDir() {
  const anchors = [
    process.env.MERGER_PROJECT_DIR,
    __dirname,
    path.dirname(process.execPath),
    process.cwd(),
  ].filter(Boolean);

  for (const anchor of anchors) {
    let dir = path.resolve(anchor);
    for (let up = 0; up < 6; up++) {
      if (fs.existsSync(path.join(dir, "docker-compose.yml"))) return dir;
      const parent = path.dirname(dir);
      if (parent === dir) break;
      dir = parent;
    }
  }
  return null;
}

const PROJECT_DIR = findProjectDir();

let win = null;
let compose = null;
let startedCompose = false;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function isUp(timeoutMs = 3000) {
  try {
    const res = await fetch(`${URL}/api/health`, { signal: AbortSignal.timeout(timeoutMs) });
    return res.ok;
  } catch {
    return false;
  }
}

/**
 * Deciding "the server is down" costs a container restart, so do not decide it
 * on one attempt. The first fetch of the process pays for undici start-up on
 * top of the Docker port proxy, and a single tight timeout reports a perfectly
 * healthy container as missing.
 */
async function isAlreadyRunning(attempts = 3) {
  for (let i = 0; i < attempts; i++) {
    if (await isUp()) return true;
    if (i < attempts - 1) await sleep(800);
  }
  return false;
}

function dockerAvailable() {
  const probe = spawnSync("docker", ["version", "--format", "{{.Server.Version}}"], {
    encoding: "utf8",
    shell: process.platform === "win32",
  });
  return probe.status === 0;
}

function startCompose() {
  // detached, and without --build: compose builds on its own when the image is
  // missing, whereas forcing a rebuild here would recreate a running container
  // and kill whatever export was in flight
  compose = spawn("docker", ["compose", "up", "-d"], {
    cwd: PROJECT_DIR,
    shell: process.platform === "win32",
    stdio: ["ignore", "pipe", "pipe"],
  });
  startedCompose = true;
  compose.stdout.on("data", (d) => process.stdout.write(`[compose] ${d}`));
  compose.stderr.on("data", (d) => process.stderr.write(`[compose] ${d}`));
}

async function waitForServer() {
  const deadline = Date.now() + START_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (await isUp()) return true;
    await sleep(1000);
  }
  return false;
}

function createWindow(loadingHtml) {
  win = new BrowserWindow({
    width: 1400,
    height: 950,
    minWidth: 980,
    backgroundColor: "#0f1116",
    title: "Helirea ühitaja",
    autoHideMenuBar: true,
    webPreferences: { nodeIntegration: false, contextIsolation: true },
  });

  // links to anywhere else belong in the real browser, not in this window
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });

  if (loadingHtml) {
    win.loadURL("data:text/html;charset=utf-8," + encodeURIComponent(loadingHtml));
  }
  return win;
}

const LOADING = `
<html><head><meta charset="utf-8"><style>
  body { margin:0; height:100vh; display:grid; place-items:center; background:#0f1116;
         color:#dfe3ec; font:14px "Segoe UI",system-ui,sans-serif; }
  .box { text-align:center; }
  h1 { font-size:16px; font-weight:600; margin:0 0 10px; }
  p { color:#8c94a8; margin:0; }
  .dots::after { content:""; animation: d 1.4s steps(4,end) infinite; }
  @keyframes d { 0%{content:""} 25%{content:"."} 50%{content:".."} 75%{content:"..."} }
</style></head><body><div class="box">
  <h1>Helirea ühitaja</h1>
  <p class="dots">Käivitan konteinerit</p>
  <p style="margin-top:8px;font-size:12px">Esimene kord võtab paar minutit — ehitab image'i</p>
</div></body></html>`;

app.whenReady().then(async () => {
  Menu.setApplicationMenu(null);
  createWindow(LOADING);

  if (!(await isAlreadyRunning())) {
    if (!PROJECT_DIR) {
      await dialog.showMessageBox(win, {
        type: "error",
        title: "Projektikausta ei leidnud",
        message: "docker-compose.yml ei ole leitav.",
        detail:
          "Hoia see aken projektikausta sees (nt dist/HelireaUhitaja/ all), " +
          "või osuta kaust keskkonnamuutujaga MERGER_PROJECT_DIR.\n\n" +
          `Teine variant: käivita server ise käsuga "docker compose up" — aken ootab ${URL}.`,
      });
      app.quit();
      return;
    }
    if (!dockerAvailable()) {
      await dialog.showMessageBox(win, {
        type: "error",
        title: "Docker puudub",
        message: "Docker ei vasta.",
        detail:
          "Käivita Docker Desktop ja proovi uuesti, või käivita server käsitsi:\n\n" +
          "    docker compose up\n\n" +
          `Aken ootab aadressi ${URL}.`,
      });
      app.quit();
      return;
    }
    startCompose();
    if (!(await waitForServer())) {
      await dialog.showMessageBox(win, {
        type: "error",
        title: "Server ei käivitunud",
        message: `${URL} ei vastanud ${START_TIMEOUT_MS / 1000} sekundi jooksul.`,
        detail: "Vaata terminali logi — ilmselt jäi image'i ehitamine pooleli.",
      });
      app.quit();
      return;
    }
  }

  win.loadURL(URL);
});

app.on("window-all-closed", () => app.quit());

app.on("before-quit", () => {
  // only tear down what this process brought up; an already-running container
  // belongs to whoever started it
  if (startedCompose && PROJECT_DIR) {
    spawnSync("docker", ["compose", "stop"], {
      cwd: PROJECT_DIR,
      shell: process.platform === "win32",
    });
  }
  compose?.kill();
});
