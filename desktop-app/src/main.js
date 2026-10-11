// TinyAssets desktop shell (Electron).
//
// A thin native window over the live SPA at https://tinyassets.io/app — the
// SAME page the Android Capacitor app wraps (mobile/capacitor.config.json). No
// second chat UI: product logic (WorkOS sign-in, connect-subscription, chat)
// lives in the SPA and is reused verbatim, so every surface stays identical and
// synced. Continuity ("the phone knows what you said on the computer") is a
// backend property — the universe + cross-turn memory are keyed on the verified
// WorkOS principal, so signing in the same user here lands in the same universe.
// See docs/design-notes/2026-08-22-desktop-app-client-surface.md.
//
// Security posture (Codex adversarial review 2026-08-23): the window renders
// REMOTE, continuously-changing code, so the shell treats the page as untrusted:
//   - hardened webPreferences (contextIsolation, no nodeIntegration, sandbox);
//   - an EXACT-origin https navigation allow-list applied to EVERY WebContents,
//     covering will-navigate, will-frame-navigate, will-redirect, and popups;
//   - shell.openExternal only for HTTP(S) URLs (never file:/custom schemes → RCE);
//   - all web permissions (camera/mic/geo/…) denied by default;
//   - the dev URL override is ignored in a packaged build.
'use strict';

const { app, BrowserWindow, session, shell, ipcMain, dialog } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const {
  resolveAppUrl,
  BACKGROUND_COLOR,
  isAllowedNavigation,
  isAllowedSubframe,
  isSafeExternal,
} = require('../config');

// ── Attachable test mode (opt-in, OFF by default) ───────────────────────────
// So a tester (or an agent) can READ and DRIVE the desktop app for QA, an
// EXPLICIT opt-in exposes Electron's remote-debugging endpoint. It is enabled
// ONLY when `--attach[=port]` is on the command line or TINYASSETS_DEVTOOLS is
// set — a normal packaged launch never opens it, so the hardened posture holds
// in production. The endpoint is bound to LOOPBACK only, and the test instance
// uses a SEPARATE user-data profile so it can run alongside the real session
// without fighting over its cookie jar. Both switches must be appended before
// app 'ready'.
function resolveAttachPort() {
  const fromEnv = (process.env.TINYASSETS_DEVTOOLS || '').trim();
  const argMatch = process.argv.find((a) => a === '--attach' || a.startsWith('--attach='));
  if (!fromEnv && !argMatch) return null;
  let raw = '';
  if (argMatch && argMatch.includes('=')) raw = argMatch.split('=')[1];
  else if (fromEnv && fromEnv !== '1') raw = fromEnv;
  const port = raw ? parseInt(raw, 10) : 9222;
  return Number.isInteger(port) && port > 0 && port < 65536 ? port : 9222;
}
const ATTACH_PORT = resolveAttachPort();
if (ATTACH_PORT !== null) {
  // Loopback only — never expose the debug endpoint off-machine. Same user-data
  // profile as a normal launch, so the tester sees the REAL signed-in session
  // (close the normal app first, then reopen via the attach shortcut).
  app.commandLine.appendSwitch('remote-debugging-port', String(ATTACH_PORT));
  app.commandLine.appendSwitch('remote-debugging-address', '127.0.0.1');
}

// Single-instance: a second launch focuses the existing window rather than
// opening a rival session that would fight over the same persistent cookie jar.
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
}

// Resolved once, before any load. A packaged build always uses the prod URL;
// only an unpackaged dev checkout may point at an explicit loopback (config.js).
const APP_URL = resolveAppUrl(app.isPackaged);

let mainWindow = null;

// Every navigation handed out of the window, as its ORIGIN only: a path or a
// query can carry a secret (reset links, OAuth codes), and a non-https URL is
// never handed out so it is never logged (Codex 2026-10-01). A sign-in hop
// missing from the allow-list looks to the user like "it sent me to the
// browser"; this file names the host.
// <userData>/navigation-handoffs.log, capped at ~64 KB.
function noteHandedOff(url) {
  try {
    const u = new URL(url);
    if (u.protocol !== 'https:') return;
    const file = path.join(app.getPath('userData'), 'navigation-handoffs.log');
    try {
      if (fs.statSync(file).size > 65536) fs.truncateSync(file, 0);
    } catch {}
    fs.appendFileSync(file, `${new Date().toISOString()} ${u.origin}\n`);
  } catch {}
}

// Route a blocked navigation target to the system browser — but ONLY if the URL
// itself is safe to hand to the OS (HTTP(S)). file:/javascript:/data:/custom
// schemes are dropped silently (openExternal on untrusted input is an RCE vector).
async function openExternalIfSafe(url) {
  if (!isSafeExternal(url)) throw new Error('A secure browser URL is required.');
  noteHandedOff(url);
  try { await shell.openExternal(url); }
  catch { throw new Error('The system browser could not open. Check your default browser and try again.'); }
}

function handOffNavigation(url) {
  if (!isSafeExternal(url)) return;
  openExternalIfSafe(url).catch((error) => dialog.showErrorBox('Could not open browser', error.message));
}

// One navigation policy for EVERY WebContents in the app (main window, OAuth
// popups, iframes), installed via web-contents-created so nothing escapes it.
function applyNavigationPolicy(contents) {
  // Top-frame + subframe navigations, and server redirects, must all land on an
  // allow-listed https origin; anything else is cancelled and (if safe) handed
  // to the system browser. Covering will-frame-navigate stops an iframe from
  // rendering a full-window phishing form inside the URL-less trusted window;
  // covering will-redirect stops an allowed OAuth endpoint 302-ing to an attacker.
  // The main frame may only show allow-listed hosts; a subframe may also show
  // subframe-only hosts. A refused MAIN-frame navigation (or redirect) is handed
  // to the system browser; a refused SUBFRAME one is cancelled silently -- a
  // page's hidden iframe is not the user asking to go somewhere (Google's
  // CheckConnection iframe opened a stray browser tab mid sign-in, 2026-10-01).
  const decide = (event, url, isMainFrame) => {
    const allowed = isMainFrame ? isAllowedNavigation(url) : isAllowedSubframe(url);
    if (allowed) return;
    event.preventDefault();
    if (isMainFrame) handOffNavigation(url);
  };
  // will-navigate fires for the main frame only.
  contents.on('will-navigate', (event, url) => decide(event, url, true));
  // will-redirect and will-frame-navigate (Electron >= 22) cover every frame;
  // event.isMainFrame names the frame being navigated.
  contents.on('will-redirect', (event) => decide(event, event.url, event.isMainFrame));
  contents.on('will-frame-navigate', (event) => decide(event, event.url, event.isMainFrame));
  // Deny ALL new windows. An allowed HTTP(S) target opens in the system browser;
  // nothing gets a fresh, policy-less WebContents inside the app.
  contents.setWindowOpenHandler(({ url }) => {
    handOffNavigation(url);
    return { action: 'deny' };
  });
}

// Deny every web permission (camera, mic, geolocation, notifications, pointer
// lock, …). This shell needs none; a compromised remote page must not be able to
// request them under the TinyAssets application identity.
function lockDownPermissions(ses) {
  ses.setPermissionRequestHandler((_wc, _permission, callback) => callback(false));
  ses.setPermissionCheckHandler(() => false);
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1100,
    height: 780,
    minWidth: 480,
    minHeight: 600,
    backgroundColor: BACKGROUND_COLOR,
    title: 'TinyAssets',
    // The TinyAssets mark (build/icon.png, exported by WebSite/brand/render_marks.py).
    // Packaged Windows/macOS builds take the icon from build/icon.{ico,icns} via
    // electron-builder; this covers Linux and `npm start`.
    icon: path.join(__dirname, '..', 'build', 'icon.png'),
    autoHideMenuBar: true,
    webPreferences: {
      // Hardened defaults: the window renders a remote page, so the renderer
      // gets no Node, an isolated context, and the OS sandbox. The preload
      // exposes only validated browser launch and app-return delivery.
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: path.join(__dirname, 'preload.js'),
    },
  });

  const loadingPage = path.join(__dirname, 'loading.html');

  function loadApp() {
    // Guard: the window can be closed/destroyed before this async step runs
    // (e.g. a fast quit), which otherwise throws "Object has been destroyed".
    if (!mainWindow || mainWindow.isDestroyed()) return;
    mainWindow.loadURL(APP_URL).catch(() => {
      if (!mainWindow || mainWindow.isDestroyed()) return;
      mainWindow.loadFile(loadingPage, { query: { offline: '1' } });
    });
  }

  // Show the loading page first for an instant window, then swap to the SPA.
  mainWindow.loadFile(loadingPage).finally(loadApp);

  // Fall back to the offline page only when the MAIN frame fails to load a
  // remote URL — a failing OAuth/helper IFRAME (or a hostile embedded frame
  // loading a dead URL) must not blow away the whole signed-in SPA.
  mainWindow.webContents.on(
    'did-fail-load',
    (_e, errorCode, _desc, validatedURL, isMainFrame) => {
      if (!isMainFrame) return;
      if (errorCode === -3) return; // ERR_ABORTED — normal during redirects
      if (validatedURL && validatedURL.startsWith('file://')) return;
      if (!mainWindow || mainWindow.isDestroyed()) return;
      mainWindow.loadFile(loadingPage, { query: { offline: '1' } });
    },
  );

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

app.on('web-contents-created', (_e, contents) => {
  applyNavigationPolicy(contents);
});

function focusApp() {
  if (mainWindow) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  }
}

// Deliver only shaped app returns, never navigate to or execute protocol input.
function isApprovalReturn(value) {
  try {
    const url = new URL(value);
    const handle = (key) => /^[A-Za-z0-9_-]{43}$/.test(url.searchParams.get(key) || '');
    return url.protocol === 'tinyassets-desktop:' && url.host === 'auth'
      && !url.username && !url.password && !url.hash && !url.pathname
      && ((url.searchParams.size === 1 && handle('completion')) ||
          (url.searchParams.size === 2 && handle('signin') && handle('return_secret')));
  } catch { return false; }
}
function isAppFrame(event) {
  try {
    const url = new URL(event.senderFrame.url), expected = new URL(APP_URL);
    return event.sender === mainWindow?.webContents && event.senderFrame === event.sender.mainFrame
      && url.origin === expected.origin && url.pathname === expected.pathname;
  } catch { return false; }
}
let pendingReturn = process.argv.find(isApprovalReturn) || null;
let returnReceiver = null;
function deliverReturn() {
  if (!pendingReturn || !returnReceiver || !isAppFrame(returnReceiver)) return;
  returnReceiver.sender.send('tinyassets:app-return', pendingReturn);
  pendingReturn = null;
}
function receiveReturn(url) {
  if (!isApprovalReturn(url)) return;
  pendingReturn = url;
  focusApp();
  deliverReturn();
}
ipcMain.handle('tinyassets:open-external', (event, url) => {
  if (!isAppFrame(event)) throw new Error('Only the app can open the browser.');
  return openExternalIfSafe(url);
});
ipcMain.on('tinyassets:return-ready', (event) => {
  if (!isAppFrame(event)) return;
  returnReceiver = event;
  deliverReturn();
});
app.on('second-instance', (_event, argv) => {
  const url = argv.find(isApprovalReturn);
  if (url) receiveReturn(url);
  else focusApp();
});
app.on('open-url', (event, url) => {
  event.preventDefault();
  receiveReturn(url);
});

app.whenReady().then(() => {
  if (app.isPackaged) app.setAsDefaultProtocolClient('tinyassets-desktop');
  lockDownPermissions(session.defaultSession);
  createWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  // Standard desktop convention: keep the app alive on macOS, quit elsewhere.
  if (process.platform !== 'darwin') app.quit();
});
