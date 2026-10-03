import type { Metadata } from "next";
import Link from "next/link";
import { RitualLabel } from "@tiny/design-system";
import { PublicShapes } from "../../components/PublicShapes";
import { SITE } from "../../lib/site";

export const metadata: Metadata = {
  title: "Commons",
  description:
    "Anything a command center publishes is a shape: a graph, a branch, a way of doing something. Copy it into your own command center and change it. Nothing published runs for anyone else.",
  alternates: { canonical: `${SITE.origin}/commons/` },
};

export default function CommonsPage() {
  return (
    <>
      <section className="container hero">
        <RitualLabel>Commons</RitualLabel>
        <h1>Shapes to remix.</h1>
        <p className="lead">
          Anything a command center publishes is a shape: a graph, a branch, a way of doing something.
          Copy it into your own command center and change it. Lineage is kept. Nothing published runs for
          anyone else.
        </p>
      </section>

      <section className="section">
        <div className="container">
          <div className="head">
            <RitualLabel>Public command centers</RitualLabel>
            <h2>What the latest snapshot showed.</h2>
            <p>
              This checked-in snapshot carries its capture date. Live discovery belongs in a
              signed-in connector. Every command center starts private; appearing here is a choice its
              owner made.
            </p>
            <p className="note">
              This is the raw list the endpoint reports as publicly discoverable, not a curated
              gallery. Early on that includes the founder&apos;s own working and housekeeping
              records alongside real command centers.
            </p>
          </div>
          <PublicShapes />
        </div>
      </section>

      <section className="section">
        <div className="container">
          <div className="head">
            <RitualLabel>How remixing works</RitualLabel>
            <h2>Copy, change, keep the lineage.</h2>
          </div>
          <div className="cols cols--3">
            <div className="col">
              <span className="num">01</span>
              <h3>Find a shape</h3>
              <p>Browse here, or ask your command center to look. A shape is the structure of a graph or branch, never anyone&apos;s contents.</p>
            </div>
            <div className="col">
              <span className="num">02</span>
              <h3>Copy it into your command center</h3>
              <p>Tell your agent to remix it. The copy is yours: it runs on your subscription, with your connections, under your control.</p>
            </div>
            <div className="col">
              <span className="num">03</span>
              <h3>Change it</h3>
              <p>Edit nodes, swap connections, add a trigger. The copy remembers where it came from, and the original is untouched.</p>
            </div>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container split">
          <div>
            <RitualLabel>What publishing means</RitualLabel>
            <h2>The shape is public. The contents stay yours.</h2>
            <p>
              Publishing shares which nodes and edges you used and how you described them. It never
              shares your documents, your credentials, or the results of your runs. You can unpublish
              or delete a shape at any time, and no one else&apos;s copy depends on yours staying up.
            </p>
          </div>
          <div>
            <RitualLabel>Nothing runs for others</RitualLabel>
            <h2 style={{ fontSize: "var(--fs-2xl)" }}>A public shape is not a service.</h2>
            <p>
              No one can invoke your published graph. They copy it and run their own. That is what
              keeps every command center isolated from every other, and it is the only hard boundary the
              platform enforces.
            </p>
            <p>
              <Link href="/start/">Start your own</Link>
            </p>
          </div>
        </div>
      </section>
    </>
  );
}
