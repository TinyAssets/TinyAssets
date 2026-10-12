import type { Metadata } from "next";
import { RitualLabel } from "@tiny/design-system";
import { SITE } from "../../lib/site";
import { CopyButton } from "./copy-button";
import {
  API_KEYS_PR,
  CLAUDE_CODE_JSON,
  CODEX_COMMANDS,
  CODEX_TOML,
  CONTACT,
  CURSOR_DEEPLINK,
  CURSOR_JSON,
  GENERIC_JSON,
  MCP_URL,
} from "./snippets.mjs";

export const metadata: Metadata = {
  title: "Connect",
  description:
    "Add the hosted TinyAssets MCP server to Claude, Cursor, Codex, or any other MCP client. Sign-in is OAuth. An API-key path is not available yet.",
  alternates: { canonical: `${SITE.origin}/connect/` },
};

export default function ConnectPage() {
  return (
    <>
      <section className="container hero">
        <RitualLabel>Connect</RitualLabel>
        <h1>Add TinyAssets to the tool you already use.</h1>
        <p className="lead">
          One address. The tool asks you to sign in to TinyAssets. Nothing on this page starts a
          server on your machine.
        </p>
        <p>
          <span className="value">{MCP_URL}</span>
        </p>
        <div className="actions">
          <CopyButton text={MCP_URL} label="Copy address" />
        </div>
      </section>

      <section className="section" id="claude">
        <div className="container">
          <div className="cols cols--2">
            <div className="sheet">
              <span className="eyebrow">Claude</span>
              <h3>Paste the address</h3>
              <p>
                In Claude.ai: Settings, then Connectors, then Add custom connector. Paste the
                address above. Sign in when Claude asks.
              </p>
              <p>
                In Claude Code, paste this MCP config. Claude Code discovers the sign-in from the
                server. Do not put a token in the file.
              </p>
              <pre>
                <code>{CLAUDE_CODE_JSON}</code>
              </pre>
              <CopyButton text={CLAUDE_CODE_JSON} label="Copy Claude Code config" />
            </div>

            <div className="sheet" id="cursor">
              <span className="eyebrow">Cursor</span>
              <h3>One click, or paste</h3>
              <p>Open Cursor with the install link, then confirm the address.</p>
              <div className="actions">
                <a className="btn btn--primary btn--md" href={CURSOR_DEEPLINK}>
                  Add to Cursor
                </a>
                <CopyButton text={CURSOR_DEEPLINK} label="Copy Cursor link" />
              </div>
              <p>Or paste this into <span className="ev">.cursor/mcp.json</span>.</p>
              <pre>
                <code>{CURSOR_JSON}</code>
              </pre>
              <CopyButton text={CURSOR_JSON} label="Copy Cursor config" />
            </div>

            <div className="sheet" id="codex">
              <span className="eyebrow">Codex</span>
              <h3>Add the server, then sign in</h3>
              <pre>
                <code>{CODEX_COMMANDS}</code>
              </pre>
              <CopyButton text={CODEX_COMMANDS} label="Copy Codex commands" />
              <p>
                The same entry in <span className="ev">~/.codex/config.toml</span>:
              </p>
              <pre>
                <code>{CODEX_TOML}</code>
              </pre>
              <CopyButton text={CODEX_TOML} label="Copy Codex config" />
            </div>

            <div className="sheet" id="generic">
              <span className="eyebrow">Any other MCP client</span>
              <h3>Streamable HTTP</h3>
              <p>
                Point the client at the same address. When it asks you to sign in, use your
                TinyAssets account.
              </p>
              <pre>
                <code>{GENERIC_JSON}</code>
              </pre>
              <CopyButton text={GENERIC_JSON} label="Copy generic config" />
            </div>
          </div>
        </div>
      </section>

      <section className="section" id="api-keys">
        <div className="container narrow">
          <RitualLabel>API keys</RitualLabel>
          <h2>A key path is proposed, and it is not live.</h2>
          <p>
            Clients that cannot sign in through OAuth cannot use a key on this address today. A
            separate <span className="ev">/api</span> door, with its own keys, is in{" "}
            <a href={API_KEYS_PR}>pull request 4602</a>. Those keys are not accepted at{" "}
            <span className="ev">{MCP_URL}</span>.
          </p>
          <p className="note">
            The model your app runs on is connected inside the app, not on this page.
          </p>
          <p>
            Questions: <a href={`mailto:${CONTACT}`}>{CONTACT}</a>
          </p>
        </div>
      </section>
    </>
  );
}
