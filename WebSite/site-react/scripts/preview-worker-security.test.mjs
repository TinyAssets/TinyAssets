import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { parse } from "yaml";

const here = dirname(fileURLToPath(import.meta.url));
const read = (relativePath) =>
  readFileSync(resolve(here, relativePath), "utf8");

const buildWorkflowText = read("../../../.github/workflows/preview-worker.yml");
const deployWorkflowText = read(
  "../../../.github/workflows/preview-worker-deploy.yml",
);
const securityWorkflowText = read(
  "../../../.github/workflows/preview-security.yml",
);
const buildWorkflow = parse(buildWorkflowText);
const deployWorkflow = parse(deployWorkflowText);
const securityWorkflow = parse(securityWorkflowText);
const trustedWorkerConfig = read("../../site-react/wrangler.preview.toml");
const trustedWorkerProgram = read("../../site-react/cf-worker/worker.js");
const trustedWorker = (
  await import(new URL("../../site-react/cf-worker/worker.js", import.meta.url))
).default;
const deployToolsPackage = JSON.parse(
  read("../../site-react/preview-deploy-tools/package.json"),
);
const deployToolsLock = JSON.parse(
  read("../../site-react/preview-deploy-tools/package-lock.json"),
);

const ACTIONS = Object.freeze({
  checkout: "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
  download:
    "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093",
  setupNode: "actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020",
  upload: "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
});
const ALLOWED_ACTIONS = new Set(Object.values(ACTIONS));

function actionSteps(workflow) {
  return Object.values(workflow.jobs).flatMap((job) =>
    job.steps.filter((step) => step.uses),
  );
}

function namedStep(job, name) {
  const step = job.steps.find((candidate) => candidate.name === name);
  assert.ok(step, `missing workflow step: ${name}`);
  return step;
}

async function invokeTrustedWorker(pathname) {
  const assetRequests = [];
  const request = new Request(`https://preview.example${pathname}`);
  const response = await trustedWorker.fetch(request, {
    ASSETS: {
      async fetch(assetRequest) {
        assetRequests.push(assetRequest);
        return new Response("asset", { status: 200 });
      },
    },
  });
  return { assetRequests, request, response };
}

test("pull-request build has one unprivileged static-export job", () => {
  assert.deepEqual(Object.keys(buildWorkflow.on), ["pull_request"]);
  assert.deepEqual(buildWorkflow.on.pull_request.paths, [
    "WebSite/site-react/**",
    "WebSite/shared/**",
    "WebSite/design-system/**",
    ".github/workflows/preview-worker.yml",
    ".github/workflows/preview-worker-deploy.yml",
  ]);
  assert.deepEqual(buildWorkflow.permissions, { contents: "read" });
  assert.deepEqual(Object.keys(buildWorkflow.jobs), ["build"]);
  assert.equal(buildWorkflow.jobs.build.environment, undefined);
  assert.doesNotMatch(buildWorkflowText, /\bsecrets\s*(?:\.|\[)/);
  assert.doesNotMatch(buildWorkflowText, /\bpull-requests:\s*write\b/);
  assert.doesNotMatch(buildWorkflowText, /\bcache\b/i);
  assert.doesNotMatch(buildWorkflowText, /\bwrangler\b/i);

  const checkout = buildWorkflow.jobs.build.steps.find(
    (step) => step.uses === ACTIONS.checkout,
  );
  assert.deepEqual(checkout.with, { "persist-credentials": false });
  const upload = buildWorkflow.jobs.build.steps.find(
    (step) => step.uses === ACTIONS.upload,
  );
  assert.deepEqual(upload.with, {
    name: "react-preview-${{ github.event.pull_request.number }}-${{ github.run_attempt }}",
    path: "WebSite/site-react/out/",
    "if-no-files-found": "error",
    "include-hidden-files": false,
    overwrite: false,
    "retention-days": 1,
  });
});

test("preview trust-boundary contract never narrows below the default PR events", () => {
  assert.deepEqual(Object.keys(securityWorkflow.on), ["pull_request", "push"]);
  // This used to assert `on.pull_request === null` as a proxy for "unfiltered".
  // #4352 added `types:` to skip drafts and did not update this test, so the
  // two contradicted each other and the check went red on main.
  //
  // The proxy was the wrong thing to pin: a types list is not automatically a
  // narrowing, and #4352's is the three GitHub defaults plus
  // `ready_for_review` -- strictly more triggering than before.
  //
  // What this file pins is PATH scope, which is what "unfiltered" means for a
  // trust boundary: no `paths`, `paths-ignore` or `branches` may appear under
  // `pull_request`, or the job stops covering PRs by what they touch. The
  // ACTIVITY-type contract (defaults present, `ready_for_review` present for
  // the draft skip to be recoverable) and the exact draft condition are owned
  // by `tests/test_ci_runner_budget.py` -- which #4352 did add, and which is
  // strictly stronger than anything assertable here. Deliberately not
  // duplicated: two authorities for one fact drift apart, and that file also
  // checks the branch-protection-context rule this one cannot see.
  const triggered = securityWorkflow.on.pull_request;
  if (triggered !== null) {
    assert.deepEqual(
      Object.keys(triggered),
      ["types"],
      "only activity types may narrow this trigger -- a path or branch " +
        "filter would stop the boundary running on every pull request",
    );
  }
  assert.deepEqual(securityWorkflow.on.push, { branches: ["main"] });
  assert.deepEqual(securityWorkflow.permissions, { contents: "read" });
  assert.deepEqual(Object.keys(securityWorkflow.jobs), ["contract"]);
  const { contract } = securityWorkflow.jobs;
  assert.equal(contract.environment, undefined);
  assert.deepEqual(contract.permissions, undefined);
  // The job's `if:` is NOT asserted here. `tests/test_ci_runner_budget.py`
  // pins it to an exact string, which catches `if: false` and every other
  // rewrite; a shape check here would be weaker and would invite the two to
  // disagree. Cross-family review found exactly that: `if: false` passed a
  // prefix match while skipping every run.
  assert.doesNotMatch(
    securityWorkflowText,
    /\bsecrets\s*(?:\.|\[)|\b(?:issues|pull-requests|actions):\s*write\b|\bcache\b|\bwrangler\b/i,
  );
  const checkout = contract.steps.find(
    (step) => step.uses === ACTIONS.checkout,
  );
  assert.deepEqual(checkout.with, { "persist-credentials": false });
  const testStep = namedStep(contract, "Run preview trust-boundary contracts");
  assert.equal(testStep["working-directory"], "WebSite/site-react");
  assert.equal(testStep.run, "npm ci\nnpm test\n");
});

test("every external action is one exact reviewed commit", () => {
  const allSteps = [
    ...actionSteps(buildWorkflow),
    ...actionSteps(deployWorkflow),
    ...actionSteps(securityWorkflow),
  ];
  assert.ok(allSteps.length > 0);
  for (const step of allSteps) {
    assert.ok(
      ALLOWED_ACTIONS.has(step.uses),
      `unreviewed action: ${step.uses}`,
    );
    assert.match(step.uses, /@[0-9a-f]{40}$/);
  }
});

test("trusted workflow-run graph keeps artifact intake outside the environment", () => {
  assert.deepEqual(Object.keys(deployWorkflow.on), ["workflow_run"]);
  assert.deepEqual(deployWorkflow.on.workflow_run, {
    workflows: ["preview-worker"],
    types: ["completed"],
  });
  assert.deepEqual(deployWorkflow.permissions, {});
  assert.equal(
    deployWorkflow.concurrency.group,
    "preview-worker-deploy-${{ github.event.workflow_run.pull_requests[0].number || github.event.workflow_run.id }}",
  );
  assert.equal(deployWorkflow.concurrency["cancel-in-progress"], false);
  assert.deepEqual(Object.keys(deployWorkflow.jobs), [
    "intake",
    "deploy",
    "comment",
  ]);

  const { intake, deploy, comment } = deployWorkflow.jobs;
  assert.equal(intake.environment, undefined);
  assert.deepEqual(intake.permissions, {
    actions: "read",
    contents: "read",
    "pull-requests": "read",
  });
  assert.deepEqual(deploy.environment, { name: "react-preview" });
  assert.deepEqual(deploy.permissions, {
    actions: "read",
    contents: "read",
    "pull-requests": "read",
  });
  assert.deepEqual(comment.permissions, { "pull-requests": "write" });
  assert.equal(comment.environment, undefined);
  assert.deepEqual(intake.outputs, {
    alias: "${{ steps.receipt.outputs.alias }}",
    head_sha: "${{ steps.receipt.outputs.head_sha }}",
    input_artifact_id: "${{ steps.receipt.outputs.artifact_id }}",
    pr_number: "${{ steps.receipt.outputs.pr_number }}",
    run_attempt: "${{ steps.receipt.outputs.run_attempt }}",
    run_id: "${{ steps.receipt.outputs.run_id }}",
    sanitized_artifact_id: "${{ steps.sanitized.outputs.artifact-id }}",
  });

  for (const job of [intake, deploy]) {
    const checkouts = job.steps.filter(
      (step) => step.uses === ACTIONS.checkout,
    );
    assert.equal(checkouts.length, 1);
    assert.equal(checkouts[0].with.ref, "${{ github.sha }}");
    assert.equal(checkouts[0].with.path, "trusted-source");
    assert.equal(checkouts[0].with["persist-credentials"], false);
  }

  const intakeDownload = intake.steps.find(
    (step) => step.uses === ACTIONS.download,
  );
  assert.equal(
    intakeDownload.with["artifact-ids"],
    "${{ steps.receipt.outputs.artifact_id }}",
  );
  assert.equal(
    intakeDownload.with["run-id"],
    "${{ github.event.workflow_run.id }}",
  );
  assert.equal(intakeDownload.with["merge-multiple"], true);
  const deployDownload = deploy.steps.find(
    (step) => step.uses === ACTIONS.download,
  );
  assert.equal(
    deployDownload.with["artifact-ids"],
    "${{ needs.intake.outputs.sanitized_artifact_id }}",
  );
  assert.equal(deployDownload.with["merge-multiple"], true);

  const intakeValidation = namedStep(
    intake,
    "Validate run, PR, workflow, and exact artifact identity",
  ).run;
  assert.match(intakeValidation, /validate-preview-run\.mjs/);
  assert.match(
    intakeValidation,
    /react-preview-\$\{PR_NUMBER\}-\$\{RUN_ATTEMPT\}/,
  );
  assert.doesNotMatch(intakeValidation, /artifact_digest=/);
  const artifactValidation = namedStep(
    intake,
    "Sanitize static export and write deterministic manifest",
  ).run;
  assert.match(artifactValidation, /validate-preview-artifact\.mjs/);
  const revalidation = namedStep(
    deploy,
    "Revalidate manifest and stage trusted Worker",
  );
  assert.equal(revalidation.id, "manifest");
  assert.equal(
    revalidation.run,
    `set -euo pipefail
deployment="\${RUNNER_TEMP}/preview-deployment"
manifest="\${RUNNER_TEMP}/preview-sanitized/manifest.json"
revalidated="\${RUNNER_TEMP}/manifest.revalidated.json"
node trusted-source/WebSite/site-react/scripts/validate-preview-artifact.mjs \\
  "\${RUNNER_TEMP}/preview-sanitized/out" \\
  "\${deployment}/out" > "\${revalidated}"
cmp "\${manifest}" "\${revalidated}"
manifest_digest="sha256:$(sha256sum "\${revalidated}" | cut -d ' ' -f 1)"
printf '%s\\n' "\${manifest_digest}" | grep -qE '^sha256:[0-9a-f]{64}$'
echo "manifest_digest=\${manifest_digest}" >> "\${GITHUB_OUTPUT}"
mkdir -p "\${deployment}/cf-worker"
cp trusted-source/WebSite/site-react/cf-worker/worker.js \\
  "\${deployment}/cf-worker/worker.js"
cp trusted-source/WebSite/site-react/wrangler.preview.toml \\
  "\${deployment}/wrangler.preview.toml"
`,
  );
});

test("Cloudflare credentials exist only on the locked per-PR version upload", () => {
  const { intake, deploy, comment } = deployWorkflow.jobs;
  const upload = namedStep(deploy, "Upload trusted per-PR preview version");
  assert.deepEqual(Object.keys(upload.env).sort(), [
    "CLOUDFLARE_ACCOUNT_ID",
    "CLOUDFLARE_API_TOKEN",
    "EXPECTED_HEAD_SHA",
    "PREVIEW_ALIAS",
    "PR_NUMBER",
  ]);
  assert.equal(
    upload.env.CLOUDFLARE_API_TOKEN,
    "${{ secrets.CLOUDFLARE_PREVIEW_API_TOKEN }}",
  );
  assert.equal(
    upload.env.CLOUDFLARE_ACCOUNT_ID,
    "${{ vars.CLOUDFLARE_PREVIEW_ACCOUNT_ID }}",
  );
  assert.deepEqual(
    deployWorkflowText.match(/\$\{\{[^}]*\bsecrets\s*(?:\.|\[)[^}]*\}\}/g),
    ["${{ secrets.CLOUDFLARE_PREVIEW_API_TOKEN }}"],
  );
  assert.doesNotMatch(
    JSON.stringify(intake),
    /CLOUDFLARE_|\bsecrets\s*(?:\.|\[)/,
  );
  assert.doesNotMatch(
    JSON.stringify(comment),
    /CLOUDFLARE_|\bsecrets\s*(?:\.|\[)/,
  );
  assert.equal(
    upload.run,
    `set -euo pipefail
test -n "\${CLOUDFLARE_API_TOKEN}"
test -n "\${CLOUDFLARE_ACCOUNT_ID}"
tool="\${GITHUB_WORKSPACE}/trusted-source/WebSite/site-react/preview-deploy-tools/node_modules/.bin/wrangler"
deployment="\${RUNNER_TEMP}/preview-deployment"
"\${tool}" versions upload \\
  --config "\${deployment}/wrangler.preview.toml" \\
  --preview-alias "\${PREVIEW_ALIAS}" \\
  --message "TinyAssets PR #\${PR_NUMBER} \${EXPECTED_HEAD_SHA}" \\
  --experimental-provision=false \\
  --experimental-auto-create=false 2>&1 \\
  | tee "\${RUNNER_TEMP}/wrangler-output.log"
`,
  );
  assert.doesNotMatch(deployWorkflowText, /\bpull_request_target\b/);
  assert.doesNotMatch(deployWorkflowText, /\bcache\b/i);
});

test("immutable preview receipt binds source artifact and Cloudflare version", () => {
  const { deploy, comment } = deployWorkflow.jobs;
  assert.deepEqual(deploy.outputs, {
    alias_url: "${{ steps.preview-url.outputs.alias_url }}",
    manifest_digest: "${{ steps.manifest.outputs.manifest_digest }}",
    url: "${{ steps.preview-url.outputs.version_url }}",
    version_id: "${{ steps.preview-url.outputs.version_id }}",
  });
  assert.doesNotMatch(
    deployWorkflowText,
    /input_artifact_digest|sanitized_artifact_digest/,
  );

  const receiptStep = namedStep(deploy, "Record immutable preview identity");
  assert.deepEqual(receiptStep.env, {
    PREVIEW_ALIAS: "${{ needs.intake.outputs.alias }}",
  });
  assert.equal(
    receiptStep.run,
    `set -euo pipefail
receipt="\${RUNNER_TEMP}/preview-upload-receipt.json"
node "\${GITHUB_WORKSPACE}/trusted-source/WebSite/site-react/scripts/validate-preview-upload.mjs" \\
  "\${RUNNER_TEMP}/wrangler-output.log" \\
  "\${PREVIEW_ALIAS}" > "\${receipt}"
{
  echo "alias_url=$(jq -r .aliasUrl "\${receipt}")"
  echo "version_id=$(jq -r .versionId "\${receipt}")"
  echo "version_url=$(jq -r .versionUrl "\${receipt}")"
} >> "\${GITHUB_OUTPUT}"
`,
  );

  const commentStep = namedStep(
    comment,
    "Recheck head and upsert preview comment",
  );
  assert.deepEqual(commentStep.env, {
    EXPECTED_HEAD_SHA: "${{ needs.intake.outputs.head_sha }}",
    GH_TOKEN: "${{ github.token }}",
    INPUT_ARTIFACT_ID: "${{ needs.intake.outputs.input_artifact_id }}",
    MANIFEST_DIGEST: "${{ needs.deploy.outputs.manifest_digest }}",
    PREVIEW_ALIAS_URL: "${{ needs.deploy.outputs.alias_url }}",
    PREVIEW_URL: "${{ needs.deploy.outputs.url }}",
    PR_NUMBER: "${{ needs.intake.outputs.pr_number }}",
    REPOSITORY: "${{ github.repository }}",
    RUN_ATTEMPT: "${{ needs.intake.outputs.run_attempt }}",
    RUN_ID: "${{ needs.intake.outputs.run_id }}",
    VERSION_ID: "${{ needs.deploy.outputs.version_id }}",
  });
  assert.match(commentStep.run, /Input artifact ID:/);
  assert.match(commentStep.run, /Sanitized deployed-tree manifest SHA-256:/);
  assert.match(commentStep.run, /Cloudflare version:/);
  assert.match(commentStep.run, /Never-reused alias:/);
});

test("privileged toolchain and Worker target are fixed by trusted files", () => {
  assert.equal(deployToolsPackage.dependencies.wrangler, "4.114.0");
  assert.equal(deployToolsLock.packages[""].dependencies.wrangler, "4.114.0");
  assert.equal(
    deployToolsLock.packages["node_modules/wrangler"].version,
    "4.114.0",
  );
  assert.match(
    deployToolsLock.packages["node_modules/wrangler"].integrity,
    /^sha512-/,
  );
  assert.match(trustedWorkerConfig, /^name = "tiny-site-react-preview"$/m);
  assert.match(trustedWorkerConfig, /^main = "cf-worker\/worker\.js"$/m);
  assert.match(trustedWorkerConfig, /^workers_dev = true$/m);
  assert.match(trustedWorkerConfig, /^preview_urls = true$/m);
  assert.doesNotMatch(
    trustedWorkerConfig,
    /^\s*(?:routes?|custom_domains?)\s*=/m,
  );
  assert.match(trustedWorkerConfig, /^directory = "\.\/out"$/m);
  assert.match(trustedWorkerConfig, /^html_handling = "auto-trailing-slash"$/m);
  assert.match(trustedWorkerConfig, /^not_found_handling = "404-page"$/m);
  assert.match(trustedWorkerConfig, /^run_worker_first = true$/m);
});

test("trusted Worker rejects MCP and discovery paths before asset lookup", async (t) => {
  const blockedPaths = [
    ["/mcp", "exact"],
    ["/mcp/", "trailing slash"],
    ["/mcp.", "trailing dot"],
    ["/mcp%2e", "encoded trailing dot"],
    ["/mcp%20", "encoded trailing space"],
    ["/mcp/tools", "descendant"],
    ["/mcp//tools", "repeated descendant slash"],
    ["//mcp", "repeated leading slash"],
    ["/MCP", "uppercase"],
    ["/mCp/tools", "mixed case descendant"],
    ["/%6dcp", "encoded m"],
    ["/m%63p", "encoded c"],
    ["/m%63%70", "encoded c and p"],
    ["/%2fmcp", "encoded leading slash"],
    ["/%5cmcp", "encoded leading backslash"],
    ["/mcp%2ftools", "encoded descendant slash"],
    ["/mcp%5ctools", "encoded descendant backslash"],
    ["/./mcp", "single dot segment"],
    ["/preview/../mcp", "parent dot segment"],
    ["/preview/%2e%2e/mcp", "encoded parent dot segment"],
    ["/mcp/.", "trailing dot segment"],
    ["/%256dcp", "double-encoded m"],
    ["/m%2563p", "double-encoded c"],
    ["/mcp%252ftools", "double-encoded slash"],
    ["/mcp%255ctools", "double-encoded backslash"],
    ["/%00/mcp", "encoded NUL prefix"],
    ["/mcp%", "trailing malformed percent"],
    ["/mcp%2", "truncated percent escape"],
    ["/mcp%zz", "non-hex percent escape"],
    [
      "/.well-known/oauth-protected-resource/mcp",
      "protected-resource discovery",
    ],
    [
      "/.WELL-KNOWN/OAUTH-PROTECTED-RESOURCE/MCP",
      "uppercase protected-resource discovery",
    ],
    ["/%2ewell-known/oauth-protected-resource/mcp", "encoded well-known dot"],
    ["/.well-known/oauth%2dprotected-resource/mcp", "encoded discovery hyphen"],
    [
      "/.well-known/oauth-protected-resource/%6dcp",
      "encoded protected-resource discovery",
    ],
    ["/.well-known/%256dcp", "double-encoded MCP discovery namespace"],
    ["/x/../.well-known/oauth-protected-resource/mcp", "dot-segment discovery"],
    ["/.well-known%5coauth-protected-resource%5cmcp", "backslash discovery"],
    [
      "/.well-known/oauth-authorization-server",
      "authorization-server discovery",
    ],
    ["/.well-known/mcp", "MCP well-known discovery"],
    ["/.well-known/mcp.json", "MCP well-known extension"],
    ["/.well-known/mcp/tools", "MCP well-known descendant"],
    ["/.well-known/openid-configuration", "OpenID discovery"],
    ["/.WELL-KNOWN/OPENID-CONFIGURATION", "uppercase OpenID discovery"],
    ["/.../mcp", "all-dot segment"],
    ["/.%20/mcp", "dot-space segment"],
    ["/asset%25name.png", "encoded literal percent"],
    ["/asset%2525name.png", "double-encoded literal percent"],
  ];

  for (const [pathname, label] of blockedPaths) {
    await t.test(label, async () => {
      const { assetRequests, response } = await invokeTrustedWorker(pathname);
      assert.equal(response.status, 503, pathname);
      assert.equal(response.headers.get("cache-control"), "no-store", pathname);
      assert.equal(assetRequests.length, 0, pathname);
    });
  }
});

test("trusted Worker delegates benign paths to assets exactly once", async (t) => {
  for (const pathname of [
    "/",
    "/_next/static/app.js",
    "/mcp-guide.html",
    "/.well-known/openai-apps-challenge",
  ]) {
    await t.test(pathname, async () => {
      const { assetRequests, request, response } =
        await invokeTrustedWorker(pathname);
      assert.equal(response.status, 200);
      assert.equal(await response.text(), "asset");
      assert.equal(assetRequests.length, 1);
      assert.equal(assetRequests[0], request);
    });
  }
});

test("trusted Worker contains no production MCP or credential bridge", () => {
  assert.doesNotMatch(trustedWorkerProgram, /tinyassets\.io\/mcp/);
  assert.doesNotMatch(
    trustedWorkerProgram,
    /\brequest\s*\.\s*headers\b|\bheaders\s*\.\s*get\s*\(|["']authorization["']|\b(?:cookie|set-cookie|www-authenticate)\b/i,
  );
});
