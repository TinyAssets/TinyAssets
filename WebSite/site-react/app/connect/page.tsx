import type { Metadata } from "next";
import { RitualLabel } from "@tiny/design-system";
// One source for every app's connect step (platform/doors/apps.json): this page, the owner
// app's connected apps page and each app's connect_url in agents_list.
import apps from "../../../../platform/doors/apps.json";
import { SITE } from "../../lib/site";
import { CopyButton } from "./copy-button";
import styles from "./connect.module.css";

interface AppEntry {
  readonly id: string;
  readonly name: string;
  readonly surfaces: readonly string[];
  readonly connect: {
    readonly link?: string;
    readonly say?: string;
    readonly steps: readonly string[];
    readonly code?: string;
  };
  readonly source: string;
  readonly checked: string;
}

const APPS = apps.apps as readonly AppEntry[];

export const metadata: Metadata = {
  title: "Connect TinyAssets",
  description: `Connect ${APPS.map((app) => app.name).join(", ")} to one TinyAssets ` +
    "workspace, so every assistant you use shares what you tell any of them.",
  alternates: { canonical: `${SITE.origin}/connect/` },
};

/** The vendor pages an app's steps were read from, one a site. */
function sources(app: AppEntry): string[] {
  const urls = app.source.split("; ").map((part) => part.split(" ")[0]!);
  return urls.filter((url, i) => urls.findIndex((other) =>
    new URL(other).hostname === new URL(url).hostname) === i);
}

export default function ConnectPage() {
  return (
    <>
      <section className="container hero">
        <RitualLabel>Connect</RitualLabel>
        <h1>One workspace for every assistant you use.</h1>
        <p className="lead">
          Connect each assistant to TinyAssets once, and sign in. What you tell one, the
          others know, and none of them does a task another already took.
        </p>
        <div className="actions">
          <a className="btn btn--primary btn--lg" href={`#${APPS[0]!.id}`}>Choose your assistant</a>
          <a className="quiet" href={SITE.app}>Open the app</a>
        </div>
      </section>

      <section className="section">
        <div className="container narrow">
          <RitualLabel>Server address</RitualLabel>
          <h2>Use this address in every app.</h2>
          <p><span className="value">{SITE.mcp}</span></p>
          <CopyButton text={SITE.mcp} label="Copy address" />
        </div>
      </section>

      <section className="section">
        <div className="container cols cols--2">
          {APPS.map((app) => (
            <div className="sheet" id={app.id} key={app.id}>
              <span className="eyebrow">{app.name}</span>
              <h2>{app.connect.say !== undefined ? "Send one sentence." : "Add the connector."}</h2>
              <p className="quiet">{app.surfaces.join(", ")}</p>
              <ol>
                {app.connect.steps.map((step) => <li key={step}>{step}</li>)}
              </ol>
              {app.connect.link !== undefined
                ? <p><a href={app.connect.link} rel="noopener noreferrer">Open {app.name}</a></p>
                : null}
              {app.connect.say !== undefined ? (
                <>
                  <pre className={styles.code}><code>{app.connect.say}</code></pre>
                  <CopyButton text={app.connect.say} label="Copy the sentence" />
                </>
              ) : null}
              {app.connect.code !== undefined ? (
                <>
                  <pre className={styles.code}><code>{app.connect.code}</code></pre>
                  <CopyButton text={app.connect.code} label="Copy" />
                </>
              ) : null}
              <p className="quiet">
                Steps from {sources(app).map((url, i) => (
                  <span key={url}>{i > 0 ? ", " : ""}<a href={url} rel="noopener noreferrer">
                    {new URL(url).hostname}</a></span>
                ))}, checked {app.checked}.
              </p>
            </div>
          ))}
        </div>
      </section>
    </>
  );
}
