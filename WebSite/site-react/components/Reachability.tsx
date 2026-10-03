"use client";

import * as React from "react";
import { fetchVitals, type Vitals } from "../lib/live";
import { fmtRel } from "../lib/fmt";
import styles from "./Reachability.module.css";

/**
 * Reachability of the public endpoint from this browser, read through the
 * public projection only (the public command center list). Server reachability and
 * workflow activity are kept as distinct readings, and a failed read is shown
 * as a failed read, never dressed up as anything else.
 */
export function Reachability() {
  const [vitals, setVitals] = React.useState<Vitals | null>(null);

  React.useEffect(() => {
    void fetchVitals().then(setVitals);
  }, []);

  return (
    <div className={styles.strip} aria-live="polite">
      <dl className="receipt" aria-label="Reachability reading">
        {!vitals && (
          <div>
            <dt>endpoint</dt>
            <dd>reading tinyassets.io/mcp from your browser…</dd>
          </div>
        )}
        {vitals?.authRequired && (
          <>
            <div>
              <dt>live readings</dt>
              <dd>sign-in required</dd>
            </div>
            <div>
              <dt>detail</dt>
              <dd>{vitals.error}</dd>
            </div>
          </>
        )}
        {vitals && !vitals.authRequired && !vitals.reachable && (
          <>
            <div>
              <dt>endpoint</dt>
              <dd className="err">unreachable from your browser</dd>
            </div>
            <div>
              <dt>detail</dt>
              <dd>{vitals.error ?? "Public MCP read is unavailable"}. This is itself a true reading.</dd>
            </div>
          </>
        )}
        {vitals && !vitals.authRequired && vitals.reachable && (
          <>
            <div>
              <dt>endpoint</dt>
              <dd className="ok">reachable</dd>
            </div>
            <div>
              <dt>public command centers</dt>
              <dd>{vitals.universeCount ?? 0}</dd>
            </div>
            <div>
              <dt>activity</dt>
              <dd>
                {vitals.workflowActive
                  ? `a public command center moved ${vitals.lastMovedAt ? fmtRel(vitals.lastMovedAt) : "recently"}`
                  : vitals.lastMovedAt
                    ? `quiet; last public movement ${fmtRel(vitals.lastMovedAt)}`
                    : "quiet; no public movement recorded"}
              </dd>
            </div>
          </>
        )}
        {vitals && (
          <div>
            <dt>read</dt>
            <dd>{fmtRel(vitals.fetchedAt)}</dd>
          </div>
        )}
      </dl>
      <div className={styles.bar}>
        <span className="note">
          Live reachability and activity stay private until a connector supplies a bearer.
        </span>
      </div>
    </div>
  );
}

export default Reachability;
