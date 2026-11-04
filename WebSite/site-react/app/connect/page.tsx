import type { Metadata } from "next";
import { RitualLabel } from "@tiny/design-system";
import { SITE } from "../../lib/site";
import { CopyButton } from "./copy-button";
import styles from "./connect.module.css";

const CODEX_COMMANDS = `codex mcp add TinyAssets --url ${SITE.mcp}
codex mcp login TinyAssets`;
const CURSOR_CONFIG = `{
  "mcpServers": {
    "TinyAssets": { "url": "${SITE.mcp}" }
  }
}`;

export const metadata: Metadata = {
  title: "Connect TinyAssets",
  description: "Add TinyAssets to Claude, ChatGPT, Cursor, or Codex with the hosted MCP server.",
  alternates: { canonical: `${SITE.origin}/connect/` },
};

export default function ConnectPage() {
  return (
    <>
      <section className="container hero">
        <RitualLabel>Connect</RitualLabel>
        <h1>Talk to your agent from the tool you use.</h1>
        <p className="lead">
          Add TinyAssets as a custom connector. Your tool sends messages to your agent and brings its
          replies back. Sign in to TinyAssets through AuthKit when prompted.
        </p>
        <div className="actions">
          <a className="btn btn--primary btn--lg" href="#claude">Choose your tool</a>
          <a className="quiet" href={SITE.app}>Open the app</a>
        </div>
      </section>

      <section className="section">
        <div className="container narrow">
          <RitualLabel>Server address</RitualLabel>
          <h2>Use this address in every tool.</h2>
          <p><span className="value">{SITE.mcp}</span></p>
          <CopyButton text={SITE.mcp} label="Copy address" />
        </div>
      </section>

      <section className="section">
        <div className="container cols cols--2">
          <div className="sheet" id="claude">
            <span className="eyebrow">Claude</span>
            <h2>Add a custom connector.</h2>
            <p>In Claude.ai, open Settings, then Connectors, then Add custom connector. Paste the server address and sign in.</p>
          </div>
          <div className="sheet" id="chatgpt">
            <span className="eyebrow">ChatGPT</span>
            <h2>Create a connector.</h2>
            <p>Open Settings, then Connectors, then Create. Paste the server address and sign in.</p>
          </div>
          <div className="sheet" id="cursor">
            <span className="eyebrow">Cursor</span>
            <h2>Add the MCP server.</h2>
            <p>In Cursor, add a remote MCP server with the address above. Or put this in <code>.cursor/mcp.json</code>, then sign in when asked.</p>
            <pre className={styles.code}><code>{CURSOR_CONFIG}</code></pre>
            <CopyButton text={CURSOR_CONFIG} label="Copy Cursor config" />
          </div>
          <div className="sheet" id="codex">
            <span className="eyebrow">Codex</span>
            <h2>Add the server and sign in.</h2>
            <p>Run these commands in your terminal:</p>
            <pre className={styles.code}><code>{CODEX_COMMANDS}</code></pre>
            <CopyButton text={CODEX_COMMANDS} label="Copy Codex commands" />
          </div>
        </div>
      </section>
    </>
  );
}
