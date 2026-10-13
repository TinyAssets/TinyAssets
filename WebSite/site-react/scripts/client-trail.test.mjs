// client-trail.test.mjs — a browser report carries the error type and its top
// frames on the caller's own cookie session. It never sends the message text
// or a session id of its choosing.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { topFrames } from "../lib/trail-frames.js";

const here = dirname(fileURLToPath(import.meta.url));
const trail = readFileSync(resolve(here, "../components/ClientTrail.tsx"), "utf8");

test("top frames drop the message line and keep file, line and column", () => {
  const stack = [
    "TypeError: Cookie: workos_session=abc123xyz ('4111111111111111', 'cus_123456789')",
    "    at render (https://tinyassets.io/_next/app.js:1:2)",
    "    at run (https://tinyassets.io/_next/app.js:3:4)",
  ].join("\n");
  const frames = topFrames(stack);
  assert.equal(
    frames,
    "at render (https://tinyassets.io/_next/app.js:1:2)\nat run (https://tinyassets.io/_next/app.js:3:4)",
  );
  assert.doesNotMatch(frames, /abc123xyz|4111111111111111|cus_123456789/);
  const firefox = "fn@https://tinyassets.io/a.js:1:2\nsecond@https://tinyassets.io/a.js:3:4";
  assert.equal(topFrames(firefox), firefox);
  assert.equal(topFrames(undefined), "");
  assert.equal(topFrames("just a message"), "");
  const deep = Array.from({ length: 20 }, (_, i) => `    at f${i} (x.js:${i}:1)`).join("\n");
  assert.equal(topFrames(deep).split("\n").length, 10);
});

test("the beacon sends frames only, on the cookie session", () => {
  assert.match(trail, /topFrames\(/);
  assert.match(trail, /credentials: "same-origin"/);
  assert.doesNotMatch(trail, /\btext\s*:/);
  assert.doesNotMatch(trail, /\.message\b/);
  assert.doesNotMatch(trail, /session_id/);
});
