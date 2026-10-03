import type { Metadata } from "next";
import Link from "next/link";
import { RitualLabel } from "@tiny/design-system";
import { Receipt } from "../../components/Receipt";
import { SITE } from "../../lib/site";
import styles from "./page.module.css";

export const metadata: Metadata = {
  title: "Build",
  description:
    "A command center does not come with integrations. It builds them from five primitives while you talk: connection, graph, code node, workspace, automation. And it keeps its own brain.",
  alternates: { canonical: `${SITE.origin}/build/` },
};

export default function BuildPage() {
  return (
    <>
      <section className="container hero">
        <RitualLabel>Build</RitualLabel>
        <h1>Five primitives. Anything you can describe.</h1>
        <p className="lead">
          A command center does not come with a list of integrations. It builds them, from a small set of
          parts, while you talk. The parts are few on purpose: they compose.
        </p>
      </section>

      <section className="section">
        <div className="container">
          <div className="ledger--wrap">
            <table className="ledger">
              <thead>
                <tr>
                  <th>Primitive</th>
                  <th>What it is</th>
                  <th>What you might say</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>Connection</td>
                  <td>
                    One generic way to reach any outside platform. Paste what you have; the command center
                    asks for what is missing, with links to where to get it.
                  </td>
                  <td className={styles.say}>&ldquo;Here is my GitHub token. Watch the issues on that repo.&rdquo;</td>
                </tr>
                <tr>
                  <td>Graph</td>
                  <td>
                    Nodes joined by edges: fetch, transform, decide, write. The unit of work, and the
                    thing you can read back as a receipt.
                  </td>
                  <td className={styles.say}>&ldquo;Every morning, fetch my calendar and draft the day.&rdquo;</td>
                </tr>
                <tr>
                  <td>Code node</td>
                  <td>
                    A sandboxed function the agent writes itself when no node fits. It sees the
                    outputs of earlier nodes and nothing else.
                  </td>
                  <td className={styles.say}>&ldquo;Count the lines and put the number in the README.&rdquo;</td>
                </tr>
                <tr>
                  <td>Workspace</td>
                  <td>
                    A checked-out repository on any forge, runnable and pushable. Scratch unless you
                    pin it, so a command center never has to be bigger than the repo it works on.
                  </td>
                  <td className={styles.say}>&ldquo;Clone it, run the tests, open a PR with the fix.&rdquo;</td>
                </tr>
                <tr>
                  <td>Automation</td>
                  <td>
                    A graph with a trigger: a schedule, a webhook, an event. It runs with your
                    authority every time, and only while you let it.
                  </td>
                  <td className={styles.say}>&ldquo;Do that every Monday until I say stop.&rdquo;</td>
                </tr>
                <tr>
                  <td>Brain</td>
                  <td>
                    What the agent knows about you and about its own work. It writes to it as it
                    learns; you can read it and correct it.
                  </td>
                  <td className={styles.say}>Nothing. It happens as you talk.</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container split">
          <div>
            <RitualLabel>A worked example</RitualLabel>
            <h2>Three nodes, written and run by the command center.</h2>
            <p>
              The founder asked for the README to carry its own line count. The command center composed a
              fetch of the file, wrote a small function to count the lines, and added a write node
              that opened the pull request. The first attempt (
              <a href={SITE.proof.firstPr} target="_blank" rel="noreferrer">
                #{SITE.proof.firstPrNumber}
              </a>
              ) used a plain replace; the second (
              <a href={SITE.proof.pr} target="_blank" rel="noreferrer">
                #{SITE.proof.prNumber}
              </a>
              ) used code it wrote itself.
            </p>
            <div className={styles.graph} aria-label="fetch, then code, then write">
              <span className={styles.node}>fetch</span>
              <span className={styles.edge} aria-hidden="true" />
              <span className={styles.node}>code</span>
              <span className={styles.edge} aria-hidden="true" />
              <span className={styles.node}>write</span>
            </div>
          </div>
          <div>
            <Receipt
              label={`Receipt for pull request ${SITE.proof.prNumber}`}
              rows={[
                { k: "node 1", v: "fetch README.md through the GitHub connection" },
                { k: "node 2", v: "compute_append: count lines in effects[fetch].body" },
                { k: "node 3", v: "github_pull_request with the new sentence" },
                { k: "result", v: "README: 91 lines.", tone: "ok" },
                { k: "outcome", v: `#${SITE.proof.prNumber} merged ${SITE.proof.mergedOn}` },
              ]}
            />
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container split">
          <div>
            <RitualLabel>Honest failure</RitualLabel>
            <h2>When it cannot, it says so, and says what would fix it.</h2>
            <p>
              A refusal is a receipt too. It names the cause and the remedy. Nothing is faked, no
              placeholder output is passed off as a result, and a run that stops says why it stopped.
            </p>
          </div>
          <div>
            <Receipt
              label="Example of a refused run"
              rows={[
                { k: "run", v: "post the summary to x.com" },
                { k: "result", v: "refused", tone: "err" },
                { k: "cause", v: "no connection to x.com holds a posting credential" },
                { k: "remedy", v: "paste an X API key in Connections, or say to skip the post" },
              ]}
            />
            <p className="note">Illustrative shape of a refusal; the wording of a real one comes from the run.</p>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <div className="head">
            <RitualLabel>Ask for real work</RitualLabel>
            <h2>The kind of thing a command center is for.</h2>
          </div>
          <div className="cols cols--3">
            <div className="col">
              <span className="num">Research</span>
              <h3>A literature review with citations</h3>
              <p>Fetch the papers, extract claims with their sources, keep the draft in your storage, revise as you read.</p>
            </div>
            <div className="col">
              <span className="num">Paperwork</span>
              <h3>An invoice pile turned into a ledger</h3>
              <p>Watch a mailbox or a folder, pull the amounts and dates, write the rows, flag what does not add up.</p>
            </div>
            <div className="col">
              <span className="num">Code</span>
              <h3>A refactor behind a pull request</h3>
              <p>Clone the repository into a workspace, run the tests, make the change, open the PR under your name.</p>
            </div>
          </div>
          <div className="actions">
            <Link className="btn btn--primary btn--md" href="/start/">
              Start
            </Link>
            <Link className="quiet" href="/commons/">
              Or remix a shape from the commons
            </Link>
          </div>
        </div>
      </section>
    </>
  );
}
