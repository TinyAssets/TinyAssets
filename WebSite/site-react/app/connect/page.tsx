import type { Metadata } from "next";
import { RitualLabel } from "@tiny/design-system";
import { checkedApps } from "../../../../platform/contract/src/apps";
import { SITE } from "../../lib/site";
import { CopyButton } from "./copy-button";
import styles from "./connect.module.css";

const catalog = checkedApps();

export const metadata: Metadata = {
  title: "Connect TinyAssets",
  description: "Choose your app and follow its instructions to connect to TinyAssets.",
  alternates: { canonical: catalog.page },
};

export default function ConnectPage() {
  return (
    <>
      <section className="container hero">
        <RitualLabel>Connect</RitualLabel>
        <h1>Talk to your agent from the tool you use.</h1>
        <p className="lead">Choose your app for setup instructions.</p>
        <nav className="actions" aria-label="Connection instructions">
          {catalog.apps.map((app) => <a key={app.id} href={`#${app.id}`}>{app.name}</a>)}
        </nav>
      </section>

      <section className="section">
        <div className="container narrow">
          <h2>MCP server address</h2>
          <p className="value">{SITE.mcp}</p>
          <CopyButton text={SITE.mcp} label="Copy server address" />
          <p><a href={SITE.app}>Open the TinyAssets app</a></p>
        </div>
      </section>

      <div className="section">
        <div className="container cols cols--2">
          {catalog.apps.map(({ id, name, surfaces, connect, source, checked }) => (
            <section className="sheet" id={id} key={id} aria-label={name}>
              <h2>{name}</h2>
              {surfaces.join(", ") !== name && <p>{surfaces.join(", ")}</p>}
              <ol>{connect.steps.map((step) => <li key={step}>{step}</li>)}</ol>
              {connect.link && <p><a href={connect.link}>Open {name}</a></p>}
              {connect.say && (
                <>
                  <blockquote>{connect.say}</blockquote>
                  <CopyButton text={connect.say} label="Copy message" />
                </>
              )}
              {connect.code && (
                <div id={surfaces.includes("Codex") ? "codex" : undefined}>
                  <pre className={styles.code}><code>{connect.code}</code></pre>
                  <CopyButton text={connect.code} label="Copy setup code" />
                </div>
              )}
              <details className={styles.sources}>
                <summary>Setup sources · checked {checked}</summary>
                <p>{source.split(/(https:\/\/\S*?)(?=;\s|\s|$)/u).map((part, i) =>
                  i % 2 === 1 ? <a key={i} href={part}>{part}</a> : part
                )}</p>
              </details>
            </section>
          ))}
        </div>
      </div>
    </>
  );
}
