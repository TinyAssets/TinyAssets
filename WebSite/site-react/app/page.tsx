import type { Metadata } from "next";
import Link from "next/link";
import { RitualLabel } from "@tiny/design-system";
import { Receipt } from "../components/Receipt";
import { SITE } from "../lib/site";
import styles from "./page.module.css";

export const metadata: Metadata = {
  alternates: { canonical: `${SITE.origin}/` },
};

export default function HomePage() {
  return (
    <>
      <section className={`container hero ${styles.hero}`}>
        <RitualLabel>TinyAssets · your own AI command center</RitualLabel>
        <h1>A command center of your own.</h1>
        <p className="lead">
          TinyAssets gives you a cloud agent that runs on the Claude or ChatGPT subscription you
          already pay for. It connects to any platform, builds any automation from a few
          primitives, learns you as it goes, and keeps working after you close the tab.
        </p>
        <div className="actions">
          <a className="btn btn--primary btn--lg" href={SITE.app}>
            Open the app
          </a>
          <Link className="quiet" href="/start/#connector">
            Add it to Claude or ChatGPT
          </Link>
          <Link className="quiet" href="/start/#android">
            Get the Android app
          </Link>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <div className="head">
            <RitualLabel>What a command center is</RitualLabel>
            <h2>An account, a hard drive, and a mind that keeps its own notes.</h2>
          </div>
          <div className="cols cols--4">
            <div className="col">
              <span className="num">01</span>
              <h3>A brain that writes itself</h3>
              <p>
                Everything the command center learns about you and about its own work goes into a brain it
                keeps updating. One brain, whichever surface you open.
              </p>
            </div>
            <div className="col">
              <span className="num">02</span>
              <h3>Connections to anything</h3>
              <p>
                Paste whatever you have for a service: an API key, a token, a webhook, a repo. The
                command center works out the rest and asks only for what is missing, with links.
              </p>
            </div>
            <div className="col">
              <span className="num">03</span>
              <h3>Automations you own</h3>
              <p>
                A recurring run is yours. It draws its authority from you every time, and you can see
                it, pause it, or delete it whenever you like.
              </p>
            </div>
            <div className="col">
              <span className="num">04</span>
              <h3>Storage and workspaces</h3>
              <p>
                Files, checkouts of repositories on any forge, receipts of every run. Kept in your
                command center and nowhere else.
              </p>
            </div>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container split">
          <div>
            <RitualLabel>It built this</RitualLabel>
            <h2>The founder&apos;s command center wrote a graph, ran it, and merged the result.</h2>
            <p>
              Asked to keep a line count in the README current, it composed three nodes: a fetch of
              the file, a small function it wrote itself to count the lines, and a write that opened
              the pull request. Nobody typed the code. The receipt on the right is what the run
              reported; the pull request is public.
            </p>
            <p>
              <a href={SITE.proof.pr} target="_blank" rel="noreferrer">
                Pull request #{SITE.proof.prNumber} on GitHub
              </a>
            </p>
          </div>
          <div>
            <Receipt
              label={`Receipt for pull request ${SITE.proof.prNumber}`}
              rows={[
                { k: "run", v: "fetch → code → write" },
                { k: "fetch", v: "README.md via the GitHub connection" },
                { k: "code", v: "compute_append: counts lines from the fetched body" },
                { k: "write", v: `pull request #${SITE.proof.prNumber}, then merged` },
                { k: "result", v: "README: 91 lines.", tone: "ok" },
                { k: "merged", v: SITE.proof.mergedOn },
              ]}
            />
            <p className="note">
              Author of record is the account whose GitHub connection the command center used. The run
              actor is the command center itself.
            </p>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container split">
          <div>
            <RitualLabel>Runs on your subscription</RitualLabel>
            <h2>No platform model. Your subscription does the thinking.</h2>
            <p>
              Sign in, then connect the subscription you already pay for. ChatGPT or Codex is one tap.
              For Claude you paste a setup token from your own account into the deposit form; it goes
              straight into your command center&apos;s vault and never through the chat.
            </p>
            <p>
              Nothing to install, no keys to manage by default, no docker. Nothing runs anywhere
              except inside a command center someone controls.
            </p>
          </div>
          <div className={styles.quiet}>
            <div className="rule">
              <span className="eyebrow">Under your control</span>
            </div>
            <ul className={styles.plain}>
              <li>Every run is visible, pausable, and deletable by you.</li>
              <li>A refusal names its cause and what would fix it. Nothing is faked.</li>
              <li>Your credentials stay in your command center. Nothing runs for another user.</li>
            </ul>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container split">
          <div>
            <RitualLabel>Commons</RitualLabel>
            <h2>Publish a shape. Others remix it into their own command center.</h2>
            <p>
              Anything you publish is a shape: a graph, a branch, a way of doing something. Someone
              else copies it into their command center and changes it, with lineage kept. Nothing you
              publish runs for anyone but you.
            </p>
            <p>
              <Link href="/commons/">See the commons</Link>
            </p>
          </div>
          <div>
            <RitualLabel>Reach it from anywhere</RitualLabel>
            <table className="ledger" style={{ marginTop: 12 }}>
              <thead>
                <tr>
                  <th>Surface</th>
                  <th>Where</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>Web app</td>
                  <td>
                    <a href={SITE.app}>tinyassets.io/app</a>
                  </td>
                </tr>
                <tr>
                  <td>Claude or ChatGPT</td>
                  <td>
                    connector at <span className="ev">tinyassets.io/mcp</span>
                  </td>
                </tr>
                <tr>
                  <td>Android</td>
                  <td>
                    <Link href="/start/#android">closed test on Google Play</Link>
                  </td>
                </tr>
                <tr>
                  <td>Desktop</td>
                  <td>
                    <Link href="/start/#desktop">unsigned build from source</Link>
                  </td>
                </tr>
              </tbody>
            </table>
            <p className="note">One brain behind all of them. Say something on your phone; it is known on your desk.</p>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <RitualLabel>Open source</RitualLabel>
          <h2>One public repository. Specs written after the fact.</h2>
          <p>
            The whole platform is on GitHub under MIT. What it does is written down as specifications
            once it works, not before, so the specs describe what actually ships.
          </p>
          <div className="actions" style={{ marginTop: 16 }}>
            <a className="btn btn--ghost btn--md" href={SITE.repo} target="_blank" rel="noreferrer">
              GitHub
            </a>
            <Link className="quiet" href="/developers/">
              For developers
            </Link>
          </div>
        </div>
      </section>
    </>
  );
}
