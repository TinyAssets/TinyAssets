// Single source of truth for the desktop shell's target + navigation policy.
// Mirrors mobile/capacitor.config.json (server.url + allowNavigation) so the
// desktop client points at the SAME live SPA the Android app wraps.
'use strict';

// The live onboarding SPA — same origin as the /mcp API and the WorkOS AuthKit
// sign-in. Authentication runs in the system browser and returns by opaque reference.
const PROD_APP_URL = 'https://tinyassets.io/app';

// Development-only override (Codex 2026-08-23 #6): a packaged build IGNORES the
// env override entirely — a launcher must never be able to start the signed
// executable pointed at an attacker page. In an UNPACKAGED dev checkout we accept
// ONLY an explicit loopback URL, validated by the caller before the first load.
function resolveAppUrl(isPackaged) {
  if (isPackaged) return PROD_APP_URL;
  const override = (process.env.TINYASSETS_APP_URL || '').trim();
  if (!override) return PROD_APP_URL;
  try {
    const u = new URL(override);
    const isLoopback =
      u.protocol === 'http:' &&
      (u.hostname === '127.0.0.1' || u.hostname === 'localhost');
    if (isLoopback) return override;
  } catch {
    /* fall through to prod */
  }
  return PROD_APP_URL;
}

// Only the app renders in-window. All identity-provider pages use the OS browser.
const EXACT_HOSTS = ['tinyassets.io'];
const BACKGROUND_COLOR = '#0f1020';
function isAllowedNavigation(urlString) {
  try {
    const u = new URL(urlString);
    return u.origin === 'https://tinyassets.io' && !u.username && !u.password;
  } catch { return false; }
}
function isAllowedSubframe(urlString) { return isAllowedNavigation(urlString); }

// A URL is safe to hand to the OS (shell.openExternal) iff it is HTTP(S). Custom
// schemes (file:, javascript:, data:, smb:, app-protocol:, …) are DENIED —
// openExternal on untrusted input is an RCE vector (Codex 2026-08-23 #1).
function isSafeExternal(urlString) {
  try {
    return ['https:', 'http:'].includes(new URL(urlString).protocol);
  } catch {
    return false;
  }
}

module.exports = {
  PROD_APP_URL,
  resolveAppUrl,
  EXACT_HOSTS,
  BACKGROUND_COLOR,
  isAllowedNavigation,
  isAllowedSubframe,
  isSafeExternal,
};
