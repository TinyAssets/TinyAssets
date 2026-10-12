"use client";

import { useState } from "react";

export function CopyAddress({ address, label }: { address: string; label: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");

  async function onCopy() {
    try {
      await navigator.clipboard.writeText(address);
      setState("copied");
    } catch {
      setState("failed");
    }
  }

  const text = state === "copied" ? "Copied" : state === "failed" ? "Copy failed" : "Copy";

  return (
    <button type="button" className="btn" onClick={onCopy} aria-label={`Copy the ${label} contract address`}>
      {text}
    </button>
  );
}
