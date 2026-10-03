import type { Metadata } from "next";
import Link from "next/link";
import { RitualLabel } from "@tiny/design-system";
import { SITE } from "../../lib/site";

export const metadata: Metadata = {
  title: "Start",
  description:
    "Sign in, connect the Claude or ChatGPT subscription you already have, and say what real thing you want finished. Web app, chatbot connector, Android and desktop.",
  alternates: { canonical: `${SITE.origin}/start/` },
};

export default function StartPage() {
  return (
    <>
      <section className="container hero">
        <RitualLabel>Start</RitualLabel>
        <h1>Set up your command center in one sitting.</h1>
        <p className="lead">
          Three steps, on whichever surface you like. Everything after that is a conversation.
        </p>
        <div className="actions">
          <a className="btn btn--primary btn--lg" href={SITE.app}>
            Open the app
          </a>
          <a className="quiet" href="#connector">
            Or add it to Claude or ChatGPT
          </a>
        </div>
      </section>

      <section className="section">
        <div className="container narrow">
          <ol className="steps">
            <li>
              <div>
                <h3>Sign in</h3>
                <p>
                  Email sign-in. The first time you arrive, a blank command center is created for you and
                  bound to your account. There is nothing to configure yet.
                </p>
              </div>
            </li>
            <li>
              <div>
                <h3>Connect your subscription</h3>
                <p>
                  Open the Connect view. ChatGPT or Codex is one tap. For Claude, paste a setup token
                  from your own Claude account into the deposit form: it goes straight into your
                  command center&apos;s vault over TLS and never passes through the chat.
                </p>
                <p className="note">
                  The platform never supplies a model. Your subscription is the only thing that runs
                  your command center. Other routes, such as your own API keys, sit behind one &ldquo;Other&rdquo;
                  control in the same view.
                </p>
              </div>
            </li>
            <li>
              <div>
                <h3>Say what real thing you want finished</h3>
                <p>
                  Not a demo. A paper, an invoice pile, a repository, a contract, a feed you want
                  watched. The agent asks for exactly what it is missing, with links, and builds
                  the rest while you talk.
                </p>
              </div>
            </li>
          </ol>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <div className="head">
            <RitualLabel>Surfaces</RitualLabel>
            <h2>One command center. Open it from wherever you are.</h2>
            <p>
              Every surface is the same command center with the same brain. Say something on your phone;
              it is already known when you sit down at the desk.
            </p>
          </div>

          <div className="cols cols--2">
            <div className="sheet" id="app">
              <span className="eyebrow">Web app</span>
              <h3>tinyassets.io/app</h3>
              <p>
                Sign in, connect, talk. This is the reference surface and the one every other
                surface wraps.
              </p>
              <a className="btn btn--primary btn--md" href={SITE.app}>
                Open the app
              </a>
            </div>

            <div className="sheet" id="connector">
              <span className="eyebrow">Claude.ai or ChatGPT</span>
              <h3>Add the connector</h3>
              <p>
                Your chatbot relays what you say to your command center, and the agent answers in its
                own voice. One URL:
              </p>
              <p>
                <span className="value">{SITE.mcp}</span>
              </p>
              <p className="note">
                Claude.ai: Settings › Connectors › Add custom connector, paste the URL. ChatGPT:
                Settings › Connectors › Create, paste the URL. Both ask you to sign in to TinyAssets
                once.
              </p>
            </div>

            <div className="sheet" id="android">
              <span className="eyebrow">Android</span>
              <h3>The app in your pocket</h3>
              <p>
                The same app, with the conversation in your notification tray so it follows you.
                It is in a closed test on Google Play: open the test page with your Google account,
                join, then install from Play. During the closed test only invited accounts can
                join; write to{" "}
                <a href={`mailto:${SITE.contact.general}`}>{SITE.contact.general}</a> to be added.
              </p>
              <a className="btn btn--ghost btn--md" href={SITE.android} target="_blank" rel="noreferrer">
                Join the test on Google Play
              </a>
              <p className="note">
                Developers: a <a href={SITE.androidDebugApk}>debug build from main</a> is published
                as an APK. It installs separately, as “TinyAssets (debug)”, and never receives Play
                updates.
              </p>
            </div>

            <div className="sheet" id="desktop">
              <span className="eyebrow">Desktop</span>
              <h3>A window on your desk</h3>
              <p>
                A thin desktop window over the same web app, for Windows, macOS and Linux. Builds are
                unsigned and come from the repository; there is no installer download yet.
              </p>
              <a className="btn btn--ghost btn--md" href={SITE.desktopSource} target="_blank" rel="noreferrer">
                Desktop app source
              </a>
            </div>
          </div>
        </div>
      </section>

      <section className="section section--tight">
        <div className="container narrow">
          <p className="note">
            Every command center starts free. Premium raises the daily limits when you need more; the{" "}
            <Link href="/fine-print/#plans">fine print</Link> says exactly what changes.
          </p>
        </div>
      </section>
    </>
  );
}
