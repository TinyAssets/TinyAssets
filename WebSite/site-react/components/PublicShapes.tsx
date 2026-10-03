"use client";

import * as React from "react";
import { PUBLIC_READ_NEEDS_SIGN_IN } from "../lib/live";
import { fmtRel, fmtStampStable, fmtCount } from "../lib/fmt";
import snapshot from "../lib/mcp-snapshot.json";
import type { Snapshot } from "../lib/types";
import { discoverableRows } from "../lib/discoverable";
import styles from "./PublicShapes.module.css";

type Row = {
  id: string;
  phase: string;
  word_count: number;
  last_activity_at: string | null;
};

const baked = snapshot as Snapshot;
const bakedRows: Row[] = discoverableRows(baked.command_centers);
const bakedStamp = `checked-in snapshot from ${fmtStampStable(baked.fetched_at)}`;

/**
 * The public command center list from a labelled checked-in snapshot. Live discovery
 * belongs to signed-in connectors; this browser never opens an MCP session.
 */
export function PublicShapes() {
  const rows = bakedRows;

  return (
    <div className={styles.wrap} aria-live="polite">
      <div className={styles.bar}>
        <span className={styles.stamp}>
          <span className="dot" aria-hidden="true" /> {PUBLIC_READ_NEEDS_SIGN_IN}; {bakedStamp}
        </span>
      </div>

      {rows.length === 0 ? (
        <p className={styles.empty}>
          No public command centers in this snapshot. Every command center starts private; publishing is a choice.
        </p>
      ) : (
        <div className="ledger--wrap">
          <table className="ledger">
            <thead>
              <tr>
                <th>Command center</th>
                <th>Phase</th>
                <th>Words</th>
                <th>Last activity</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td>
                    <span className="ev">{r.id}</span>
                  </td>
                  <td>{r.phase}</td>
                  <td>
                    <span className="ev">{fmtCount(r.word_count)}</span>
                  </td>
                  <td>
                    <span className="ev">{r.last_activity_at ? fmtRel(r.last_activity_at) : "—"}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="note">
        A signed-in connector may read the public projection: command center ids and coarse activity.
        This page never downloads operator status or anyone&apos;s contents.
      </p>
    </div>
  );
}

export default PublicShapes;
