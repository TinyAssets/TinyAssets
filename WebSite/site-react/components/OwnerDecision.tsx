import * as React from "react";

/**
 * A visible gap for a fact the site must not invent. The label is part of
 * the page, so a visitor can see the sentence is unfinished.
 */
export function OwnerDecision({ children }: { children: React.ReactNode }) {
  return (
    <p className="owner-decision">
      <span className="eyebrow">Owner decision</span>
      <span>{children}</span>
    </p>
  );
}
