import type { Metadata } from "next";
import { RitualLabel } from "@tiny/design-system";
import profiles from "../../../../platform/contract/profiles.json";
import { SITE } from "../../lib/site";

export const metadata: Metadata = {
  title: "Developers",
  description:
    "One public repository under MIT. The MCP endpoint and its tool profiles, the specs written after things work, the design system, and how to contribute.",
  alternates: { canonical: `${SITE.origin}/developers/` },
};

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
              Connect an MCP client here, then call <code>tools/list</code> to see the tools
              available to that connection. The owner can narrow the custom tool list or turn
              off code mode. Each call also requires the appropriate grant.
            </p>
            <p>
              <a href={SITE.connectorSpec} target="_blank" rel="noreferrer">
                Read the tool descriptions and arguments
              </a>
            </p>
          </div>
          <section aria-labelledby="tool-profiles">
            <h3 id="tool-profiles">MCP tool profiles</h3>
            <p>
              Connector grants use the custom profile by default. A grant assigned the
              directory profile uses that tool list. An agent running in a sandbox uses the run profile.
            </p>
            {Object.entries(profiles).map(([name, profile]) => (
              <details key={name}>
                <summary>{name} ({profile.tools.length})</summary>
                <ul>
                  {profile.tools.map(tool => <li key={tool}><code>{tool}</code></li>)}
                </ul>
              </details>
            ))}
          </section>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <div className="head">
            <RitualLabel>Local development</RitualLabel>
            <h2>The source path.</h2>
            <p>
              Use the Node.js version specified in <code>platform/package.json</code>, then run
              the TypeScript checker from the platform workspace.
            </p>
          </div>
          <div className="cols cols--2">
            <div>
              <pre>
                <code>{`git clone ${SITE.repo}.git
cd TinyAssets/platform
npm ci
npm run typecheck`}</code>
              </pre>
            </div>
            <div>
              <table className="ledger">
                <tbody>
                  <tr>
                    <td>Design</td>
                    <td>
                      <a href={SITE.plan} target="_blank" rel="noreferrer">
                        docs/architecture.md
                      </a>{" "}
                      is how the system works.
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
            Security reports go to <a href={`mailto:${SITE.contact}`}>{SITE.contact}</a>.
          </p>
        </div>
      </section>
    </>
  );
}
