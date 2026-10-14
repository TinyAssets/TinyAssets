import type { Metadata } from "next";
import Link from "next/link";
import { RitualLabel } from "@tiny/design-system";
import { OwnerDecision } from "../components/OwnerDecision";
import { SITE } from "../lib/site";
import styles from "./page.module.css";

export const metadata: Metadata = {
  alternates: { canonical: `${SITE.origin}/` },
  description:
    "TinyAssets is one app on any device. You bring a model you already use. Your agents keep your work. Early access is open.",
};

export default function HomePage() {
  return (
    <>
      <section className={`container ${styles.screen}`} aria-labelledby="home-title">
        <RitualLabel>Early access</RitualLabel>
        <h1 id="home-title">One app. Your agents. Your model.</h1>
        <p className="lead">
          TinyAssets is one app on the web, on a phone, and on a desk. You bring a model you
          already use. Your agents keep your files and your work. A setup you publish can be
          copied into someone else&apos;s app. The platform does not supply a model.
        </p>
        <div className={styles.facts}>
          <article className={styles.fact}>
            <h2>One app</h2>
            <p>The web app, an Android closed test, and an unsigned desktop window. Same account.</p>
          </article>
          <article className={styles.fact}>
            <h2>Your agents</h2>
            <p>They keep notes, files, and runs you can see, pause, or delete.</p>
          </article>
          <article className={styles.fact}>
            <h2>Bring a model</h2>
            <p>ChatGPT or Codex is one tap. Claude is a setup token you paste. Other API keys sit behind one Other control.</p>
          </article>
          <article className={styles.fact}>
            <h2>Share a setup</h2>
            <p>Publishing a shape lets someone copy it. Nothing you publish runs as them.</p>
          </article>
        </div>
        <div className="actions">
          <Link className="btn btn--primary btn--lg" href="/start/">
            Join early access
          </Link>
          <a className="quiet" href={SITE.app}>
            Open the app
          </a>
          <a className="quiet" href="#how">
            How it works
          </a>
          <Link className="quiet" href="/start/#connector">
            Add it to Claude or ChatGPT
          </Link>
          <Link className="quiet" href="/start/#android">
            Get the Android app
          </Link>
          <a className="quiet" href={`${SITE.app}?upgrade=1`}>
            Plans
          </a>
        </div>
      </section>

      <section className="section" id="how">
        <div className="container">
          <div className="head">
            <RitualLabel>How it works</RitualLabel>
            <h2>Sign in, connect a model, say what to finish.</h2>
          </div>
          <ol className="steps">
            <li>
              <div>
                <h3>Sign in</h3>
                <p>Email sign-in. The first time you arrive, a blank app is created for your account.</p>
              </div>
            </li>
            <li>
              <div>
                <h3>Bring a model</h3>
                <p>
                  ChatGPT or Codex is one tap. For Claude, paste a setup token from your own account.
                  It goes into your vault over TLS. The platform never supplies a model.
                </p>
              </div>
            </li>
            <li>
              <div>
                <h3>Say what you want finished</h3>
                <p>
                  A paper, a repository, a feed you want watched. The agent asks for what is missing,
                  with links, and keeps the run where you can see it.
                </p>
              </div>
            </li>
          </ol>
          <p style={{ marginTop: 20 }}>
            <Link href="/build/">The parts an agent builds from</Link>
          </p>
        </div>
      </section>

      <section className="section" id="join">
        <div className="container">
          <div className="head">
            <RitualLabel>Join</RitualLabel>
            <h2>Early access is the product that exists today.</h2>
            <p>
              The web app is open. Android is a closed test on Google Play, and only invited
              accounts can join. Desktop builds are unsigned and come from the repository. There
              is no public Play listing and no signed installer.
            </p>
          </div>
          <div className="actions">
            <Link className="btn btn--primary btn--md" href="/start/">
              See the join steps
            </Link>
            <a className="quiet" href={`mailto:${SITE.contact}`}>
              {SITE.contact}
            </a>
          </div>
        </div>
      </section>

      <section className="section" id="status">
        <div className="container">
          <div className="head">
            <RitualLabel>Status and price</RitualLabel>
            <h2>Free means the same product and the same security.</h2>
            <p>
              Tiers, when they exist, differ only by how many runs can be in flight at once and by
              how much you can store. Nothing else is held back. Paid tiers are not on sale, and
              this page does not name a price.
            </p>
          </div>
          <div className="ledger--wrap">
            <table className="ledger">
              <thead>
                <tr>
                  <th>Tier</th>
                  <th>Product and security</th>
                  <th>What can differ</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>Free, today</td>
                  <td>The same app and the same security as every other tier.</td>
                  <td>Simultaneous runs and storage. The numbers are not published.</td>
                </tr>
                <tr>
                  <td>Paid</td>
                  <td>The same app and the same security. Not on sale.</td>
                  <td>Simultaneous runs and storage. No price is set on this site.</td>
                </tr>
              </tbody>
            </table>
          </div>
          <OwnerDecision>
            Set the free-tier simultaneous-run cap and storage cap, and any paid tier names,
            prices, and caps, before this table grows past the placeholder. Do not add an upgrade
            button until the app has one.
          </OwnerDecision>
          <p className="note">
            The full statement, including what is not enforced today, is on the{" "}
            <Link href="/fine-print/#plans">fine print</Link>.
          </p>
        </div>
      </section>

      <section className="section" id="creators">
        <div className="container split">
          <div>
            <RitualLabel>Creators</RitualLabel>
            <h2>Publish a setup. Someone else copies it.</h2>
            <p>
              A published shape is a graph, a branch, or a way of doing something. Another person
              copies it into their own app and changes it. Lineage is kept. The copy does not run
              as you, and it does not use your credentials.
            </p>
            <p>
              <Link href="/commons/">See what is public</Link>
            </p>
            <OwnerDecision>
              If “shareable setups” should mean something other than copying a published shape,
              replace this paragraph. The site does not claim a directory listing or a count of
              shared setups.
            </OwnerDecision>
          </div>
          <div>
            <RitualLabel>Power users</RitualLabel>
            <h2>Abilities are files.</h2>
            <p>
              There is no feature editor on this site. What an agent can do is edited as files in
              the repository. The source page says how to read the code and the specifications.
            </p>
            <p>
              <Link href="/developers/">Open source</Link>
            </p>
          </div>
        </div>
      </section>

      <section className="section" id="source">
        <div className="container">
          <RitualLabel>Open source</RitualLabel>
          <h2>One public repository, MIT.</h2>
          <p>
            The platform code is on GitHub. Specifications describe what has shipped. The only
            public connector URL is <span className="ev">tinyassets.io/mcp</span>.
          </p>
          <div className="actions">
            <a className="btn btn--ghost btn--md" href={SITE.repo} target="_blank" rel="noreferrer">
              GitHub
            </a>
            <Link className="quiet" href="/developers/">
              For people who want the source
            </Link>
            <Link className="quiet" href="/fine-print/">
              Fine print
            </Link>
          </div>
        </div>
      </section>
    </>
  );
}
