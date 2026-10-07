/**
 * Canonical public addresses the site links to. One list, so a moved surface
 * is a one-line change and the copy never drifts from the real URLs.
 */
export const SITE = {
  origin: "https://tinyassets.io",
  /** The web app: sign in, connect a subscription, talk to your universe. */
  app: "https://tinyassets.io/app",
  /** The one public MCP endpoint (Claude.ai / ChatGPT connector URL). */
  mcp: "https://tinyassets.io/mcp",
  repo: "https://github.com/TinyAssets/TinyAssets",
  /**
   * The Android app: Google Play's opt-in page for the closed test. This is the
   * only install path the site offers people -- a Play install keeps updating.
   */
  android: "https://play.google.com/apps/testing/io.tinyassets.app",
  /**
   * Developer debug build from main. It installs separately, as
   * io.tinyassets.app.debug ("TinyAssets (debug)"), because it is signed with a
   * development key: sharing the Play package made every Play update fail.
   */
  androidDebugApk:
    "https://github.com/TinyAssets/TinyAssets/releases/download/android-latest/app-debug.apk",
  desktopSource: "https://github.com/TinyAssets/TinyAssets/tree/main/desktop-app",
  specs: "https://github.com/TinyAssets/TinyAssets/tree/main/openspec/specs",
  connectorSpec:
    "https://github.com/TinyAssets/TinyAssets/blob/main/openspec/specs/live-mcp-connector-surface/spec.md",
  designSystem: "https://github.com/TinyAssets/TinyAssets/tree/main/WebSite/design-system",
  plan: "https://github.com/TinyAssets/TinyAssets/blob/main/docs/architecture.md",
  agents: "https://github.com/TinyAssets/TinyAssets/blob/main/AGENTS.md",
  /** The receipt on the home page: the founder's universe merged this itself. */
  proof: {
    pr: "https://github.com/TinyAssets/TinyAssets/pull/2728",
    prNumber: 2728,
    mergedOn: "2026-08-30",
    firstPr: "https://github.com/TinyAssets/TinyAssets/pull/2720",
    firstPrNumber: 2720,
  },
  contact: {
    general: "ops@tinyassets.io",
    security: "security@tinyassets.io",
    legal: "legal@tinyassets.io",
  },
} as const;

export const NAV = [
  { href: "/start/", label: "Start" },
  { href: "/build/", label: "Build" },
  { href: "/commons/", label: "Commons" },
  { href: "/developers/", label: "Developers" },
  { href: "/fine-print/", label: "Fine print" },
] as const;
