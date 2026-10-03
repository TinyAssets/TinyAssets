import type { Metadata } from "next";
import { RitualLabel } from "@tiny/design-system";
import { SITE } from "../../lib/site";

export const metadata: Metadata = {
  title: "Developers",
  description:
    "One public repository under MIT. The MCP endpoint and its seven handles, the specs written after things work, the design system, and how to contribute.",
  alternates: { canonical: `${SITE.origin}/developers/` },
};

const HANDLES: Array<[string, string]> = [
  ["converse", "Talk to a command center. With no graph id, it resolves the caller's home command center."],
  ["read_graph", "Read graphs, branches, connections, runs, and the public command center list."],
  ["write_graph", "Create or change a graph, branch, connection, or automation in your command center."],
  ["run_graph", "Run a graph now and get the receipt."],
  ["read_page", "Read a page of durable state, or list what changed since a time."],
  ["write_page", "Write a page of durable state."],
  ["get_status", "Operator-facing status. Supporting evidence, never the opening move."],
];

export default function DevelopersPage() {
  return (
    <>
      <section className="container hero">
        <RitualLabel>Developers</RitualLabel>
        <h1>Open source, spec-driven.</h1>
        <p className="lead">
          The platform is one public repository under MIT. What it does is written down as
          specifications after it works, not before, so the specs describe what actually ships.
        </p>
        <div className="actions">
          <a className="btn btn--primary btn--lg" href={SITE.repo} target="_blank" rel="noreferrer">
            GitHub
          </a>
          <a className="quiet" href={SITE.specs} target="_blank" rel="noreferrer">
            Read the specs
          </a>
        </div>
      </section>

      <section className="section">
        <div className="container split">
          <div>
            <RitualLabel>The endpoint</RitualLabel>
            <h2>One public MCP endpoint.</h2>
            <p>
              <span className="value">{SITE.mcp}</span>
            </p>
            <p>
              JSON-RPC over HTTP with an initialised session. Claude.ai and ChatGPT use it as a
              connector; the web, iOS, Android and desktop apps use it too. There is no other public
              endpoint, and anything claiming to be one is not us.
            </p>
            <p>
              <a href={SITE.connectorSpec} target="_blank" rel="noreferrer">
                Connector surface spec
              </a>
            </p>
          </div>
          <div>
            <RitualLabel>Seven handles</RitualLabel>
            <table className="ledger" style={{ marginTop: 12 }}>
              <thead>
                <tr>
                  <th>Handle</th>
                  <th>Does</th>
                </tr>
              </thead>
              <tbody>
                {HANDLES.map(([name, does]) => (
                  <tr key={name}>
                    <td>
                      <span className="ev">{name}</span>
                    </td>
                    <td>{does}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <div className="head">
            <RitualLabel>Run it yourself</RitualLabel>
            <h2>The source path.</h2>
            <p>
              You do not need any of this to use TinyAssets; the hosted command center runs on your
              subscription. This is for reading, contributing, or running the engine on your own
              machine.
            </p>
          </div>
          <div className="cols cols--2">
            <div>
              <pre>
                <code>{`git clone ${SITE.repo}.git
cd TinyAssets
python -m venv .venv && . .venv/bin/activate   # Python 3.11+
pip install -e .
tinyassets-mcp        # the MCP server
tinyassets-cli        # the command line`}</code>
              </pre>
            </div>
            <div>
              <table className="ledger">
                <tbody>
                  <tr>
                    <td>Design</td>
                    <td>
                      <a href={SITE.plan} target="_blank" rel="noreferrer">
                        PLAN.md
                      </a>{" "}
                      is how the system works and why.
                    </td>
                  </tr>
                  <tr>
                    <td>Process</td>
                    <td>
                      <a href={SITE.agents} target="_blank" rel="noreferrer">
                        AGENTS.md
                      </a>{" "}
                      is how to work here; every change gets a review from a second model family.
                    </td>
                  </tr>
                  <tr>
                    <td>Specs</td>
                    <td>
                      <a href={SITE.specs} target="_blank" rel="noreferrer">
                        openspec/specs
                      </a>{" "}
                      holds as-built behaviour, one capability per file.
                    </td>
                  </tr>
                  <tr>
                    <td>Design system</td>
                    <td>
                      <a href={SITE.designSystem} target="_blank" rel="noreferrer">
                        @tiny/design-system
                      </a>
                      : DTCG tokens, React components, Storybook. This site is built on it.
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container narrow">
          <RitualLabel>Contribute</RitualLabel>
          <h2>Pull requests, with the receipt attached.</h2>
          <p>
            Open a pull request against <span className="ev">main</span>. The checks that gate a
            merge are public in the repository. A good change ships with the evidence it worked:
            a test, a live run, or a rendered conversation. A command center can open one too; the first
            uncoached pull request from a user&apos;s command center landed in August 2026.
          </p>
          <p>
            Security reports go to <a href={`mailto:${SITE.contact.security}`}>{SITE.contact.security}</a>.
          </p>
        </div>
      </section>
    </>
  );
}
