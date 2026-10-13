"use client";

import { Component, useEffect, type ReactNode } from "react";

import { topFrames } from "../lib/trail-frames";

const COOKIE = "ta_session";

function readSession(): string {
  const found = document.cookie.match(/(?:^|; )ta_session=(s_[0-9a-f]{32})(?:;|$)/);
  if (found) return found[1];
  const id = `s_${crypto.randomUUID().replace(/-/g, "")}`;
  const secure = window.location.protocol === "https:" ? "; Secure" : "";
  document.cookie = `${COOKIE}=${id}; Path=/; Max-Age=2592000; SameSite=Lax${secure}`;
  return id;
}

// The session is the ta_session cookie the request carries; the server binds
// the report to it. Only the error name and its top frames are sent: an error
// message is free text and may hold anything the page held.
function send(kind: string, fields: { name?: string; frames?: string }) {
  readSession();
  const body = JSON.stringify({
    kind,
    path: window.location.pathname,
    name: fields.name || "",
    frames: fields.frames || "",
  });
  fetch("/app/client-error", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body,
    keepalive: true,
    credentials: "same-origin",
  }).catch(() => undefined);
}

function Beacon() {
  useEffect(() => {
    send("page.load", {});
    const onError = (event: ErrorEvent) => {
      const fault = event.error instanceof Error ? event.error : null;
      send("window.error", {
        name: fault?.name || "Error",
        frames: topFrames(fault?.stack),
      });
    };
    const onRejection = (event: PromiseRejectionEvent) => {
      const fault = event.reason instanceof Error ? event.reason : null;
      send("unhandledrejection", {
        name: fault?.name || "UnhandledRejection",
        frames: topFrames(fault?.stack),
      });
    };
    window.addEventListener("error", onError);
    window.addEventListener("unhandledrejection", onRejection);
    return () => {
      window.removeEventListener("error", onError);
      window.removeEventListener("unhandledrejection", onRejection);
    };
  }, []);
  return null;
}

class PageBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(caught: unknown) {
    const fault = caught instanceof Error ? caught : null;
    send("react", {
      name: fault?.name || "Error",
      frames: topFrames(fault?.stack),
    });
  }

  render() {
    if (this.state.failed) return <p>This page could not be shown.</p>;
    return this.props.children;
  }
}

export default function ClientTrail({ children }: { children: ReactNode }) {
  return (
    <>
      <Beacon />
      <PageBoundary>{children}</PageBoundary>
    </>
  );
}
