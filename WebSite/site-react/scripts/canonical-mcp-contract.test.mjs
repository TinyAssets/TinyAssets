import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  assertSnapshotEndpoint,
  assertPublicBrowserEndpoint,
  assertPublicPlaygroundCall,
  assertCompleteCrawl,
  pageInventoryCall,
  publicGraphCall,
  publicPageCall,
  requireCollection,
  requireCompleteCollection,
  requireObjectResult,
  requirePageBody,
  sanitizePublicPlaygroundResponse,
  splitFullPageInventory,
  splitPageInventory,
} from "../../shared/mcp/public-read-contract.js";
import * as publicReadContract from "../../shared/mcp/public-read-contract.js";

const here = dirname(fileURLToPath(import.meta.url));
const snapshotSourcePath = resolve(here, "snapshot-public.mjs");
const reactSourceRoot = resolve(here, "..");
const sharedSourceRoot = resolve(here, "../../shared");
const nextConfigPath = resolve(here, "../next.config.mjs");
const reactDeployWorkflowPath = resolve(
  here,
  "../../../.github/workflows/deploy-site-react.yml",
);
const previewWorkflowPath = resolve(
  here,
  "../../../.github/workflows/preview-worker.yml",
);

function readPublicSourceTree(directory) {
  return readdirSync(directory, { withFileTypes: true })
    .flatMap((entry) => {
      if ([".next", "build", "node_modules", "out"].includes(entry.name)) {
        return [];
      }
      const path = resolve(directory, entry.name);
      if (entry.isDirectory()) return [readPublicSourceTree(path)];
      if (!/\.(?:[cm]?[jt]sx?)$/.test(entry.name)) return [];
      return [readFileSync(path, "utf8")];
    })
    .join("\n");
}

test("canonical public read descriptors allowlist only proven-safe graph discovery", () => {
  assert.deepEqual(pageInventoryCall(), {
    name: "read_page",
    args: {
      changed_since: "1970-01-01T00:00:00Z",
      max_results: 100,
    },
  });
  assert.deepEqual(pageInventoryCall("2026-07-27T00:00:00Z"), {
    name: "read_page",
    args: {
      changed_since: "2026-07-27T00:00:00Z",
      max_results: 100,
    },
  });
  assert.deepEqual(publicGraphCall("graphs", 8), {
    name: "read_graph",
    args: { target: "graphs", limit: 8 },
  });
  for (const unsafeTarget of ["goal", "goals", "run", "runs"]) {
    assert.throws(
      () => publicGraphCall(unsafeTarget, 8),
      /only supports target=graphs/i,
    );
  }
  for (const invalidLimit of [0, 101, 1.5, "8"]) {
    assert.throws(
      () => publicGraphCall("graphs", invalidLimit),
      /limit.*1-100/i,
    );
  }
  assert.equal("publicGoalCall" in publicReadContract, false);
  assert.equal("publicRunCall" in publicReadContract, false);
});

test("public Playground execution contract accepts only bounded discovery reads", () => {
  assert.doesNotThrow(() =>
    assertPublicPlaygroundCall("read_graph", { target: "graphs", limit: 100 }),
  );
  assert.doesNotThrow(() =>
    assertPublicPlaygroundCall("read_page", pageInventoryCall().args),
  );

  for (const [tool, args] of [
    ["get_status", {}],
    ["get_status", { command_center_id: "private-universe" }],
    ["read_graph", { target: "goals", limit: 100 }],
    ["read_graph", { target: "graphs", limit: 101 }],
    ["read_graph", { target: "graphs", limit: 100, command_center_id: "private" }],
    ["read_page", { page: "pages/plans/private-coordination" }],
    ["read_page", { query: "private coordination" }],
    ["read_page", { category: "plans" }],
    ["read_page", { changed_since: "1970-01-01T00:00:00Z", max_results: 99 }],
    ["read_page", { ...pageInventoryCall().args, scope: "all" }],
    ["unknown_tool", {}],
  ]) {
    assert.throws(
      () => assertPublicPlaygroundCall(tool, args),
      /public playground/i,
      `${tool} ${JSON.stringify(args)} must fail closed`,
    );
  }
});

test("snapshot URL requires a bare credential-free HTTPS endpoint", () => {
  assert.equal(
    assertSnapshotEndpoint("https://tinyassets.io/mcp"),
    "https://tinyassets.io/mcp",
  );
  for (const unsafe of [
    "https://user:token@tinyassets.io/mcp",
    "https://user@tinyassets.io/mcp",
    "https://tinyassets.io/mcp?access_token=top-secret",
    "https://tinyassets.io/mcp?TOKEN=top-secret",
    "https://tinyassets.io/mcp?api-key=top-secret",
    "https://tinyassets.io/mcp?key=top-secret",
    "https://tinyassets.io/mcp?auth=top-secret",
    "https://tinyassets.io/mcp?authorization=Bearer%20top-secret",
    "https://tinyassets.io/mcp?signature=top-secret",
    "https://tinyassets.io/mcp?sig=top-secret",
    "https://tinyassets.io/mcp?credential=top-secret",
    "https://tinyassets.io/mcp?password=top-secret",
    "https://tinyassets.io/mcp?x-amz-credential=top-secret",
    "https://tinyassets.io/mcp?x-amz-signature=top-secret",
    "https://tinyassets.io/mcp?session_token=top-secret",
    "https://tinyassets.io/mcp?oauth_token=top-secret",
    "https://tinyassets.io/mcp?client_secret=top-secret",
    "https://tinyassets.io/mcp?id_token_hint=eyJhbGciOiJIUzI1NiJ9",
    "https://tinyassets.io/mcp?client_assertion=eyJhbGciOiJIUzI1NiJ9",
    "https://tinyassets.io/mcp?SAMLResponse=assertion",
    "https://tinyassets.io/mcp?ticket=service-ticket",
    "https://tinyassets.io/mcp?AWSAccessKeyId=AKIAEXAMPLE",
    "https://tinyassets.io/mcp?CF_Authorization=opaque",
    "https://tinyassets.io/mcp?device_code=device-value",
    "https://tinyassets.io/mcp?code_verifier=verifier-value",
    "https://tinyassets.io/mcp?jwt=top-secret",
    "https://tinyassets.io/mcp?authorization_code=top-secret",
    "https://tinyassets.io/mcp#access_token=top-secret",
    "https://tinyassets.io/mcp#/callback?refresh_token=top-secret",
    "https://tinyassets.io/mcp#bearer=top-secret",
    "https://tinyassets.io/mcp#Bearer%20top-secret",
    "https://tinyassets.io/mcp#access_token%3Dabc123",
    "https://tinyassets.io/mcp#callback%3Frefresh_token%3Dabc123",
    "https://tinyassets.io/mcp#session_id%3Dabc123",
    "https://tinyassets.io/mcp#access_token%253Dabc123",
    "https://tinyassets.io/mcp?code=abc123",
    "https://tinyassets.io/mcp?access_key=abc123",
    "https://tinyassets.io/mcp?label=Bearer%20top-secret",
    "https://tinyassets.io/mcp?%2574oken=top-secret",
    "https://tinyassets.io/mcp?next=%253Ftoken%253Dtop-secret",
    "https://tinyassets.io/mcp?redirect=%2Fcb%3Faccess_token%3Dtop-secret",
    "https://tinyassets.io/mcp?next=token%253Dtop-secret",
    "https://tinyassets.io/mcp?next=%2561ccess_token%253Dtop-secret",
    "https://tinyassets.io/mcp?redirect=cb%2523token%253Dtop-secret%2526next%253Dok",
    "https://tinyassets.io/mcp#redirect=cb%2523token%253Dtop-secret%2526next%253Dok",
    "https://tinyassets.io/mcp?next=https%3A%2F%2Fuser%3Atop-secret%40internal.example%2Fcb",
    "https://tinyassets.io/mcp#next=https%3A%2F%2Fuser%3Atop-secret%40internal.example%2Fcb",
    "https://tinyassets.io/mcp?next=%2F%2Fuser%3Atop-secret%40internal.example%2Fcb",
    "https://tinyassets.io/mcp?next=%20https%3A%2F%2Fuser%3Atop-secret%40internal.example%2Fcb",
    "https://tinyassets.io/mcp?next=%09https%3A%2F%2Fuser%3Atop-secret%40internal.example%2Fcb",
    "https://tinyassets.io/mcp?next=https%3A%5C%5Cuser%3Atop-secret%40internal.example%2Fcb",
    "https://tinyassets.io/mcp?next=https%3A%2F%5Cuser%3Atop-secret%40internal.example%2Fcb",
    "https://tinyassets.io/mcp?next=https%3Auser%3Atop-secret%40internal.example%2Fcb",
    "https://tinyassets.io/mcp?next=https%3A%2Fuser%3Atop-secret%40internal.example%2Fcb",
    "https://tinyassets.io/mcp?next=https%3A%5Cuser%3Atop-secret%40internal.example%2Fcb",
    "https://tinyassets.io/mcp?next=https%3A%2F%2Fuser%3Atop-secret%40%5B",
    "https://tinyassets.io/mcp?next=%2F%2Fuser%3Atop-secret%40%5B",
    "https://tinyassets.io/mcp#next=https%3A%2F%2Fuser%3Atop-secret%40%5B",
    "https://tinyassets.io/mcp?next=ssh%3A%2F%2Fuser%3Ahunter2%40%5B",
    "https://tinyassets.io/mcp#next=ftps%3A%2F%2Fuser%3Ahunter2%40%5B",
    "https://tinyassets.io/mcp?next=git%2Bssh%3A%2F%2Fuser%3Ahunter2%40%5B",
    "https://tinyassets.io/mcp?next=h%09ttps%3A%2F%2Fuser%3Ahunter2%40%5B",
    "https://tinyassets.io/mcp#next=https%3A%2F%0A%2Fuser%3Ahunter2%40%5B",
    "https://tinyassets.io/mcp?next=https%3A%2F%2F%2Fuser%3Ahunter2%40%5B",
    "https://tinyassets.io/mcp?next=ssh%3A%2Fuser%3Ahunter2%40host.invalid%2Fcb",
    "https://tinyassets.io/mcp?next=ssh%3Auser%3Ahunter2%40host.invalid%2Fcb",
    "https://tinyassets.io/mcp#next=ssh%3A%2F%2F%2Fuser%3Ahunter2%40host.invalid%2Fcb",
    "https://tinyassets.io/mcp?next=git%2Bssh%3A%5Cuser%3Ahunter2%40host.invalid%2Fcb",
    "https://tinyassets.io/mcp#next=g%09it%2Bssh%3A%2Fuser%3Ahunter2%40host.invalid%2Fcb",
    "https://alice:s3cr3t@[::1",
    `https://tinyassets.io/mcp#${Array.from({ length: 10 }).reduce(
      (value) => encodeURIComponent(value),
      "?token=top-secret",
    )}`,
  ]) {
    assert.throws(
      () => assertSnapshotEndpoint(unsafe),
      /credentials|bare MCP URL/i,
    );
  }
  const excessivelyEncodedCredential = Array.from({ length: 20 }).reduce(
    (value) => encodeURIComponent(value),
    "?token=top-secret",
  );
  assert.throws(
    () =>
      assertSnapshotEndpoint(
        `https://tinyassets.io/mcp#${excessivelyEncodedCredential}`,
      ),
    /bare MCP URL/i,
  );
  for (const insecureSnapshotUrl of [
    "http://tinyassets.io/mcp",
    "file:///tmp/tinyassets-mcp",
  ]) {
    assert.throws(
      () => assertSnapshotEndpoint(insecureSnapshotUrl),
      /HTTPS MCP URL/i,
    );
  }
  assert.equal(assertPublicBrowserEndpoint("/mcp"), "/mcp");
  for (const unsafeBrowserEndpoint of [
    "//token@evil.example/mcp",
    String.raw`/\\evil.example/mcp`,
    "http://tinyassets.io/mcp",
    "https://tinyassets.io/mcp?mode=public",
    "https://tinyassets.io/mcp?code=abc123",
    `/mcp?next=${encodeURIComponent("h\tttps://alice:hunter2@[")}`,
    `/mcp?next=${encodeURIComponent("https:/\n/alice:hunter2@[")}`,
    `/mcp?next=${encodeURIComponent("ssh:/alice:hunter2@host.invalid/cb")}`,
    `/mcp?next=${encodeURIComponent("ssh:alice:hunter2@host.invalid/cb")}`,
    `/mcp?next=${encodeURIComponent(
      "g\tit+ssh:/alice:hunter2@host.invalid/cb",
    )}`,
    `https://tinyassets.io/mcp#next=${encodeURIComponent(
      "git://alice:hunter2@host.invalid:99999/cb",
    )}`,
  ]) {
    assert.throws(() => assertPublicBrowserEndpoint(unsafeBrowserEndpoint));
  }
});

test("public Playground responses are validated and reduced to public discovery fields", () => {
  assert.deepEqual(
    sanitizePublicPlaygroundResponse(
      "read_graph",
      { target: "graphs", limit: 100 },
      {
        command_centers: [
          {
            id: "public-one",
            visibility: "public",
            has_premise: true,
            has_soul: false,
            word_count: 12,
            phase: "idle",
            phase_human: "Idle",
            staleness: "fresh",
            last_activity_at: "2026-07-27T00:00:00Z",
            accept_rate: 1,
            local_path: "C:\\Users\\operator\\private",
            auth_health: { bearer: "secret" },
          },
        ],
        count: 1,
        note: "Base directory: C:\\Users\\operator\\private",
      },
    ),
    {
      command_centers: [
        {
          id: "public-one",
          visibility: "public",
          has_premise: true,
          has_soul: false,
          word_count: 12,
          phase: "idle",
          phase_human: "Idle",
          staleness: "fresh",
          last_activity_at: "2026-07-27T00:00:00Z",
          accept_rate: 1,
        },
      ],
      count: 1,
    },
  );

  assert.deepEqual(
    sanitizePublicPlaygroundResponse(
      "read_page",
      pageInventoryCall().args,
      {
        results: [
          {
            path: "pages/concepts/public.md",
            title: "Public",
            type: "concept",
            updated: "2026-07-27T00:00:00Z",
            is_draft: false,
            excerpt: "A published excerpt.",
            content: "private body must not reach the Playground",
          },
        ],
        count: 1,
        total_matches: 1,
        truncated_count: 0,
        scope: "discovery",
        scope_note: "Coordination pages are omitted.",
        operator_state: { token: "secret" },
      },
    ),
    {
      results: [
        {
          path: "pages/concepts/public.md",
          title: "Public",
          type: "concept",
          updated: "2026-07-27T00:00:00Z",
          is_draft: false,
          excerpt: "A published excerpt.",
        },
      ],
      count: 1,
      total_matches: 1,
      truncated_count: 0,
      scope: "discovery",
      scope_note: "Discovery scope reports omitted coordination pages.",
    },
  );

  assert.throws(
    () =>
      sanitizePublicPlaygroundResponse(
        "read_graph",
        { target: "graphs", limit: 100 },
        { command_centers: "not-an-array" },
      ),
    /command_centers array/i,
  );
  assert.throws(
    () =>
      sanitizePublicPlaygroundResponse(
        "read_page",
        pageInventoryCall().args,
        {
          results: [],
          count: 0,
          total_matches: 0,
          truncated_count: 0,
          scope: "all",
          scope_note: "",
        },
      ),
    /incomplete/i,
  );
  assert.throws(
    () =>
      sanitizePublicPlaygroundResponse(
        "read_graph",
        { target: "graphs", limit: 100 },
        {
          command_centers: [{ id: "private-one", visibility: "private" }],
          count: 1,
        },
      ),
    /explicit discoverable visibility/i,
  );
  assert.deepEqual(
    sanitizePublicPlaygroundResponse(
      "read_graph",
      { target: "graphs", limit: 2 },
      {
        command_centers: [{ id: "metadata-one", visibility: "metadata_only" }],
        count: 1,
      },
    ),
    {
      command_centers: [{ id: "metadata-one", visibility: "metadata_only" }],
      count: 1,
    },
  );
  assert.throws(
    () =>
      sanitizePublicPlaygroundResponse(
        "read_graph",
        { target: "graphs", limit: 1 },
        {
          command_centers: [
            { id: "public-one", visibility: "public" },
            { id: "public-two", visibility: "public" },
          ],
          count: 2,
        },
      ),
    /over-limit/i,
  );
  for (const incomplete of [
    { total_matches: 999 },
    { total: 999 },
    { truncated_count: 1 },
    { truncated: true },
    { has_more: true },
    { next_cursor: "private-next-page" },
  ]) {
    assert.throws(
      () =>
        sanitizePublicPlaygroundResponse(
          "read_graph",
          { target: "graphs", limit: 100 },
          {
            command_centers: [{ id: "public-one", visibility: "public" }],
            count: 1,
            ...incomplete,
          },
        ),
      /incomplete collection/i,
    );
  }
});

test("exact page descriptors require an immutable validated inventory provenance", () => {
  const inventory = splitPageInventory({
    results: [
      { path: "pages/concepts/example.md", title: "Example", is_draft: false },
    ],
    count: 1,
    total_matches: 1,
    truncated_count: 0,
    scope: "discovery",
    scope_note: "Default discovery scope omitted coordination pages.",
  });

  assert.deepEqual(publicPageCall("pages/concepts/example.md", inventory.validatedPaths), {
    name: "read_page",
    args: { page: "pages/concepts/example" },
  });
  assert.throws(
    () => publicPageCall("pages/plans/private-coordination", inventory.validatedPaths),
    /validated inventory/i,
  );
  assert.throws(
    () => publicPageCall("pages/concepts/example", new Set(["pages/concepts/example"])),
    /validated inventory/i,
  );
  inventory.validatedPaths.add("pages/plans/private-coordination");
  assert.throws(
    () => publicPageCall("pages/plans/private-coordination", inventory.validatedPaths),
    /validated inventory/i,
    "mutating the exposed set must not widen its validated provenance",
  );
});

test("page inventory accepts explicit discovery scope with bounded omission copy", () => {
  const inventory = splitPageInventory({
      results: [
        { path: "pages/concepts/one.md", title: "One", is_draft: false },
        { path: "drafts/notes/two.md", title: "Two", is_draft: true },
      ],
      count: 2,
      total_matches: 2,
      truncated_count: 0,
      scope: "discovery",
      scope_note: "Default discovery scope omitted coordination pages.",
    });
  assert.deepEqual(inventory.promoted, [
    { path: "pages/concepts/one.md", title: "One", is_draft: false },
  ]);
  assert.deepEqual(inventory.drafts, [
    { path: "drafts/notes/two.md", title: "Two", is_draft: true },
  ]);
  assert.deepEqual(
    [...inventory.validatedPaths],
    ["pages/concepts/one", "drafts/notes/two"],
  );
  assert.equal(
    inventory.scopeNote,
    "Discovery scope reports omitted coordination pages.",
  );
});

test("full snapshot inventory requires independent audience-safe publication evidence", () => {
  assert.throws(
    () =>
      splitFullPageInventory({
        results: [
          {
            path: "pages/plans/operator-secret.md",
            title: "Operator Secret",
            is_draft: false,
          },
        ],
        count: 1,
        total_matches: 1,
        truncated_count: 0,
        scope: "all",
        scope_note: "",
      }),
    /independent audience-safe publication evidence/i,
  );

  assert.throws(
    () =>
      splitFullPageInventory({
        results: [],
        count: 0,
        total_matches: 0,
        truncated_count: 0,
        scope: "discovery",
        scope_note: "Default discovery scope omitted coordination pages.",
      }),
    /full snapshot.*incomplete/i,
  );
});

test("public clients and snapshot logs never surface untrusted error detail", () => {
  const client = readFileSync(resolve(here, "../lib/live.ts"), "utf8");
  assert.doesNotMatch(client, /json\.error\.message|res\.statusText/);
  assert.doesNotMatch(client, /error:\s*error(?:\?\.|\.)(?:message|stack)/);
  assert.match(client, /signed-in connector/);
  const snapshotSource = readFileSync(snapshotSourcePath, "utf8");
  assert.doesNotMatch(snapshotSource, /\.(?:message|stack)\b/);
  assert.doesNotMatch(
    snapshotSource,
    /tool \$\{name\}.*JSON\.stringify\(args\).*e\.message|refresh failed: \$\{/,
  );
  assert.match(snapshotSource, /Required public snapshot refresh failed/);
  assert.match(snapshotSource, /TINYASSETS_WIKI_CANARY_TOKEN/);
  assert.match(snapshotSource, /Authorization:\s*`Bearer \$\{TOKEN\}`/);
});

test("page inventory rejects non-discovery scope and handles a missing omission note", () => {
  assert.throws(
    () =>
      splitPageInventory({
        results: [],
        count: 0,
        total_matches: 0,
        truncated_count: 0,
        scope: "all",
        scope_note: "",
      }),
    /incomplete/i,
  );
  assert.throws(
    () =>
      splitPageInventory({
        results: [],
        count: 0,
        total_matches: 0,
        truncated_count: 0,
        scope: "coordination",
        scope_note: "Only coordination pages were returned.",
      }),
    /incomplete/i,
  );
  assert.equal(
    splitPageInventory({
      results: [],
      count: 0,
      total_matches: 0,
      truncated_count: 0,
      scope: "discovery",
      scope_note: "",
    }).scopeNote,
    "Discovery scope reports no omitted coordination pages.",
  );
  for (const scopeNote of [
    42,
    { private: "operator detail" },
    ["omitted"],
  ]) {
    assert.throws(
      () =>
        splitPageInventory({
          results: [],
          count: 0,
          total_matches: 0,
          truncated_count: 0,
          scope: "discovery",
          scope_note: scopeNote,
        }),
      /invalid scope metadata/i,
    );
  }
});

test("page inventory rejects truncation and inconsistent completeness metadata", () => {
  assert.throws(
    () =>
      splitPageInventory({
        results: [],
        count: 0,
        total_matches: 101,
        truncated_count: 101,
        scope: "discovery",
        scope_note: "Default discovery scope omitted coordination pages.",
      }),
    /truncated 101 of 101/,
  );
  assert.throws(
    () =>
      splitPageInventory({
        results: [{ path: "pages/concepts/one.md", is_draft: false }],
        count: 1,
        total_matches: 2,
        truncated_count: 0,
        scope: "all",
        scope_note: "",
      }),
    /inconsistent completeness metadata/,
  );
});

test("page inventory rejects coerced completeness metadata values", () => {
  for (const field of ["count", "total_matches", "truncated_count"]) {
    for (const invalidValue of [null, "0", false]) {
      const payload = {
        results: [],
        count: 0,
        total_matches: 0,
        truncated_count: 0,
        scope: "discovery",
        scope_note: "Default discovery scope omitted coordination pages.",
        [field]: invalidValue,
      };
      assert.throws(
        () => splitPageInventory(payload),
        /inconsistent completeness metadata/,
        `${field}=${JSON.stringify(invalidValue)} must be rejected`,
      );
    }
  }
});

test("page inventory fails closed when the response exactly fills the request cap", () => {
  const results = Array.from({ length: 100 }, (_, index) => ({
    path: `pages/concepts/page-${index}.md`,
    is_draft: false,
  }));
  assert.throws(
    () =>
      splitPageInventory({
        results,
        count: 100,
        total_matches: 100,
        truncated_count: 0,
        scope: "discovery",
        scope_note: "Default discovery scope omitted coordination pages.",
      }),
    /cannot prove completeness.*request limit of 100/i,
  );
  const overLimitResults = Array.from({ length: 101 }, (_, index) => ({
    path: `pages/concepts/over-limit-${index}.md`,
    is_draft: false,
  }));
  assert.throws(
    () =>
      splitPageInventory({
        results: overLimitResults,
        count: 101,
        total_matches: 101,
        truncated_count: 0,
        scope: "discovery",
        scope_note: "Default discovery scope omitted coordination pages.",
      }),
    /cannot prove completeness.*request limit of 100/i,
  );
  for (const continuation of [
    { has_more: true },
    { next_cursor: "private-next-page" },
  ]) {
    assert.throws(
      () =>
        splitPageInventory({
          results: [],
          count: 0,
          total_matches: 0,
          truncated_count: 0,
          scope: "discovery",
          scope_note: "Default discovery scope omitted coordination pages.",
          ...continuation,
        }),
      /incomplete collection/i,
    );
  }
});

test("page inventory rejects missing scope and structured errors", () => {
  assert.throws(
    () =>
      splitPageInventory({
        results: [],
        count: 0,
        total_matches: 0,
        truncated_count: 0,
      }),
    /incomplete/i,
  );
  assert.throws(
    () => splitPageInventory({ error: "read denied" }),
    /read_page inventory returned an error/,
  );
});

test("canonical collection reads reject structured errors and missing arrays", () => {
  assert.deepEqual(
    requireCollection({ goals: [{ id: "g-1" }] }, "goals", "read_graph goals"),
    [{ id: "g-1" }],
  );
  assert.deepEqual(
    requireObjectResult({ release_state: {} }, "get_status"),
    { release_state: {} },
  );
  assert.throws(
    () => requireCollection({ error: "denied" }, "goals", "read_graph goals"),
    /read_graph goals returned an error/,
  );
  assert.throws(
    () => requireCollection({}, "command_centers", "read_graph graphs"),
    /command_centers array/,
  );
  assert.throws(
    () => requireObjectResult({ error: "unavailable" }, "get_status"),
    /get_status returned an error/,
  );
  assert.deepEqual(
    requirePageBody({ content: "# Public page" }, "read_page body"),
    { content: "# Public page" },
  );
  assert.throws(
    () => requirePageBody({ error: "not found" }, "read_page body"),
    /read_page body returned an error/,
  );
  assert.throws(
    () => requirePageBody({}, "read_page body"),
    /content string/,
  );
  const provenPage = {
    path: "pages/concepts/public.md",
    is_draft: false,
    content: "# Public page",
    truncated: false,
    source_read_proof: {
      path: "pages/concepts/public.md",
      is_draft: false,
      sha256: "a".repeat(64),
    },
  };
  assert.deepEqual(
    requirePageBody(
      provenPage,
      "read_page body",
      "pages/concepts/public",
    ),
    provenPage,
  );
  for (const unproven of [
    { ...provenPage, path: "pages/private.md" },
    {
      ...provenPage,
      source_read_proof: {
        ...provenPage.source_read_proof,
        path: "pages/private.md",
      },
    },
    {
      ...provenPage,
      source_read_proof: {
        ...provenPage.source_read_proof,
        sha256: "not-a-hash",
      },
    },
    { ...provenPage, truncated: true },
  ]) {
    assert.throws(
      () =>
        requirePageBody(
          unproven,
          "read_page body",
          "pages/concepts/public",
        ),
      /different page path|source-read proof|completeness proof/i,
    );
  }
});

test("snapshot collections fail closed when an unpageable request fills its cap", () => {
  assert.deepEqual(
    requireCompleteCollection(
      { command_centers: [{ id: "u-1" }], count: 1 },
      "command_centers",
      "read_graph graphs",
      100,
    ),
    [{ id: "u-1" }],
  );
  assert.throws(
    () =>
      requireCompleteCollection(
        {
          command_centers: Array.from({ length: 100 }, (_, index) => ({
            id: `u-${index}`,
          })),
          count: 100,
        },
        "command_centers",
        "read_graph graphs",
        100,
      ),
    /cannot prove completeness.*limit of 100/i,
  );
  for (const inconsistent of [
    { command_centers: [{ id: "u-1" }], count: 2 },
    { command_centers: [{ id: "u-1" }], count: "1" },
    { command_centers: [{ id: "u-1" }], count: 1, total_matches: 2 },
    { command_centers: [{ id: "u-1" }], count: 1, total: 2 },
    { command_centers: [{ id: "u-1" }], count: 1, truncated_count: 1 },
    { command_centers: [{ id: "u-1" }], count: 1, has_more: true },
  ]) {
    assert.throws(
      () =>
        requireCompleteCollection(
          inconsistent,
          "command_centers",
          "read_graph graphs",
          100,
        ),
      /inconsistent|incomplete/i,
    );
  }
});

test("snapshot page crawl cannot succeed with skipped or failed bodies", () => {
  assert.doesNotThrow(() => assertCompleteCrawl(2, 2, 0));
  assert.throws(() => assertCompleteCrawl(2, 1, 0), /attempted 1 of 2/);
  assert.throws(() => assertCompleteCrawl(2, 2, 1), /1 page read failed/);
  const snapshotSource = readFileSync(snapshotSourcePath, "utf8");
  assert.doesNotMatch(snapshotSource, /SNAPSHOT_MAX_PAGES|MAX_CRAWL_PAGES/);
  // The baker reads exactly what the browser reads and fails closed on an
  // unproven collection; it bakes no pages, goals, or operator status.
  assert.match(snapshotSource, /\brequireCompleteCollection\s*\(/);
  assert.match(snapshotSource, /\bsanitizePublicUniverse\b/);
  assert.match(snapshotSource, /publicGraphCall\(\s*["']graphs["']/);
  assert.doesNotMatch(snapshotSource, /read_page|get_status|publicPageCall|splitFullPageInventory/);
});

test("website readers contain no calls to retired MCP tool names", () => {
  const source =
    readPublicSourceTree(reactSourceRoot) + "\n" + readPublicSourceTree(sharedSourceRoot);

  assert.doesNotMatch(
    source,
    /(?:tool|callTool)\(\s*['"](?:wiki|goals|universe|extensions)['"]/,
  );
  const retiredObjectCall =
    /callTool\(\s*\{\s*name:\s*['"](?:wiki|goals|universe|extensions)['"]/;
  assert.doesNotMatch(source, retiredObjectCall);
  for (const name of ["wiki", "goals", "universe", "extensions"]) {
    assert.match(`callTool({ name: '${name}', arguments: {} })`, retiredObjectCall);
  }
  assert.doesNotMatch(source, /\b(?:wiki|goals|universe|extensions)\s+action=/);
});

test("the home page renders no live goal reader", () => {
  const home = readFileSync(resolve(here, "../app/page.tsx"), "utf8");
  assert.doesNotMatch(home, /\bfetchLive\b/);
  assert.doesNotMatch(home, /\bfetchPublicGoals?\b/);
});

test("the authenticated boundary gates both preview and deploy", () => {
  // The proxy remains available to authenticated application work, while the
  // public browser module is statically prevented from calling it.
  const nextConfig = readFileSync(nextConfigPath, "utf8");
  assert.match(
    nextConfig,
    /source:\s*["']\/mcp["'],\s*destination:\s*["']https:\/\/tinyassets\.io\/mcp["']/,
  );

  const reactDeploy = readFileSync(reactDeployWorkflowPath, "utf8");
  const preview = readFileSync(previewWorkflowPath, "utf8");
  assert.match(reactDeploy, /working-directory:\s*WebSite\/site-react[\s\S]*npm test/);
  assert.match(preview, /working-directory:\s*WebSite\/site-react[\s\S]*npm test/);
  assert.match(preview, /WebSite\/shared\/\*\*/);

  const reactLive = readFileSync(resolve(here, "../lib/live.ts"), "utf8");
  assert.match(reactLive, /authRequired:\s*true/);
  assert.doesNotMatch(reactLive, /\bfetch\s*\(|assertPublicBrowserEndpoint\(/);
});

test("explicit snapshot refresh reports a refused refresh as failure", () => {
  const snapshotSource = readFileSync(snapshotSourcePath, "utf8");
  assert.match(snapshotSource, /SNAPSHOT_REQUIRED/);
  assert.match(snapshotSource, /process\.exit\(1\)/);
});
