/**
 * Production workflows run only inside the main-only `production` environment.
 *
 * Incident: a `workflow_dispatch` on a branch runs that branch's YAML, so a job-level
 * `if: github.ref == 'refs/heads/main'` guards nothing: the branch can delete the line.
 * Repo-level secrets are reachable from any ref, and run 38484527257 used them to scp branch
 * code onto the production host as root. The fix is structural: the production secrets live
 * in a GitHub Environment named `production` whose deployment branches are restricted to
 * `main` (docs/host-actions.md). A job only receives them when it declares
 * `environment: production`, and GitHub refuses that job on any other ref before a step runs.
 *
 * Conservative: EVERY secret is a production secret unless it is on the justified allowlist
 * below, and a job also counts as production when it opens a shell to a host
 * (ssh/scp/rsync/sftp), inherits the caller's secrets, or runs a repo-local action that
 * reads a secret. Each such job declares `environment: production`.
 *
 * The same rule is the pull-request guard (concern 2026-07-27, P0). A same-repo
 * `pull_request` run executes the PR's own YAML and gets every repository secret, so a job
 * a PR reaches may read only the harmless ones: a job that names a main-only environment
 * is refused on the PR's `refs/pull/N/merge` ref, and GitHub hands a fork's run no secret
 * at all. A `pull_request_target` run executes main's YAML with the secrets, so it may name
 * an environment, but then it must not check out the PR's code (below).
 */
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";
import { parse } from "yaml";

import { REPO } from "../paths";
import { type Data, workflow, workflowNames, WORKFLOWS } from "../workflows";

/**
 * The harmless secrets: the only ones a job may read WITHOUT a main-only environment, so the
 * only ones any branch's code can reach, a pull request's included. Each changes nothing
 * outside GitHub and grants no more than the push access a branch's author already holds.
 * They stay at repository scope; every other secret lives in an environment
 * (docs/host-actions.md). Every entry says why; an entry no workflow uses fails, so the
 * list cannot accrete.
 */
const HARMLESS_SECRETS: Record<string, string> = {
  GITHUB_TOKEN: "per-run token GitHub mints, scoped by `permissions:`; it can do in the " +
    "repository no more than the pusher whose branch ran it, and a fork's `pull_request` run " +
    "gets it read-only (pull_request_target's is not: see its rule below)",
  TINYASSETS_WIKI_CANARY_TOKEN: "bearer for the read-only `canary` service principal; the " +
    "daemon 403s anything beyond get_status / read_graph status " +
    "(docs/reference/environment-variables.md)",
  CLOUDFLARE_ACCOUNT_ID: "Cloudflare account identifier, not a credential (AGENTS.md)",
  CLOUDFLARE_ZONE_ID: "Cloudflare zone identifier, not a credential",
  TOKEN_HEX: "python `secrets.token_hex(...)` in a run script, not a stored secret",
};

/**
 * Secrets held by a main-only environment of their own, never by `production`: a job that
 * reads one declares that environment, and no other job may read it.
 */
const ENVIRONMENT_SECRETS: Record<string, string> = {
  // The review App's key: the only identity that may post the required `review` check
  // (review.yml). Its environment deploys from main alone, with no reviewer.
  REVIEW_APP_PRIVATE_KEY: "review",
  // The Apple release pipeline's: each use waits for the environment's required review
  // (docs/host-actions.md), which `production`'s scheduled jobs could not.
  APPLE_DISTRIBUTION_CERT_P12_B64: "app-store",
  APPLE_DISTRIBUTION_CERT_PASSWORD: "app-store",
  APPLE_PROVISIONING_PROFILE_B64: "app-store",
  APP_STORE_CONNECT_API_KEY_ID: "app-store",
  APP_STORE_CONNECT_API_KEY_B64: "app-store",
  APP_STORE_CONNECT_API_ISSUER_ID: "app-store",
  APP_STORE_REVIEW_USERNAME: "app-store",
  APP_STORE_REVIEW_PASSWORD: "app-store",
  AWS_DEVICEFARM_ACCESS_KEY_ID: "app-store",
  AWS_DEVICEFARM_SECRET_ACCESS_KEY: "app-store",
  AWS_DEVICEFARM_PROJECT_ARN: "app-store",
};

// The step shapes that take a pull request's own code into a run (GitHub's "Securely using
// pull_request_target"): a checkout of its head, merge ref or repository, or `gh pr checkout`.
// Fetching the head to read a diff (pr-scope-guard.yml, review.yml) runs nothing.
const PULL_REQUEST_CODE = /github\.event\.pull_request\.head\.|github\.head_ref|refs\/pull\//;
const GH_PR_CHECKOUT = /\bgh\s+pr\s+checkout\b/;

/** Workflows known to hold production jobs: guards the scan against matching nothing. */
const KNOWN_PRODUCTION_WORKFLOWS = [
  "apply-daemon-env.yml", "box-preflight.yml", "cloud-only-preflight.yml", "deploy-prod.yml",
  "diagnose-prod-startup.yml", "dr-drill.yml", "droplet-resize.yml", "fetch-prod-logs.yml",
  "import-window.yml", "install-host-services.yml", "offsite-copy.yml", "p0-outage-triage.yml",
  "platform-canary.yml", "platform-deploy.yml", "restart-daemon.yml", "sandbox-template.yml",
  "uptime-canary.yml",
];

// `secrets.NAME`, `secrets['NAME']`, `secrets["NAME"]`, `secrets[NAME]`,
// `secrets[format(...)]`: any key, any quoting. A dynamic index is reported as the raw
// expression, which is never on the allowlist.
const SECRET_REF = /\bsecrets\s*(?:\.\s*([A-Za-z_][A-Za-z0-9_]*)|\[\s*(['"]?)([^\]]*?)\2\s*\])/g;
// The whole secrets context at once.
const SECRETS_CONTEXT = /\btoJSON\s*\(\s*secrets\s*\)/i;
// A remote shell or file copy at command position (`ssh-keyscan`, `#do-ssh-key` and
// `DO_SSH_KEY` do not match).
const HOST_SHELL = /(?:^|[\s;&|(`$])(ssh|scp|rsync|sftp)\s/m;

/** Every string in `value`, keys included, one a line: what a secret reference hides in. */
function strings(value: Data): string {
  if (typeof value === "string") {
    return value;
  }
  if (Array.isArray(value)) {
    return value.map(strings).join("\n");
  }
  if (value && typeof value === "object") {
    return Object.entries(value).map(([key, each]) => `${key}\n${strings(each)}`).join("\n");
  }
  return String(value ?? "");
}

function secretNames(text: string): Set<string> {
  const names = new Set<string>();
  for (const match of text.matchAll(SECRET_REF)) {
    names.add((match[1] ?? match[3] ?? "").trim());
  }
  if (SECRETS_CONTEXT.test(text)) {
    names.add("<toJSON(secrets)>");
  }
  return names;
}

function productionSecretNames(text: string): string[] {
  return [...secretNames(text)].filter((name) => !(name.toUpperCase() in HARMLESS_SECRETS)
                                                 && !(name.toUpperCase() in ENVIRONMENT_SECRETS))
    .sort();
}

/** Every local action's text (`action.yml` or `action.yaml` under `.github/actions`), by folder. */
function actionTexts(): [string, string][] {
  const actions = join(REPO, ".github", "actions");
  const out: [string, string][] = [];
  for (const folder of existsSync(actions) ? readdirSync(actions) : []) {
    for (const name of ["action.yml", "action.yaml"]) {
      if (existsSync(join(actions, folder, name))) {
        out.push([folder, readFileSync(join(actions, folder, name), "utf8")]);
      }
    }
  }
  return out;
}

function localAction(uses: string, repo: string): string | null {
  if (!uses.startsWith("./")) {
    return null;
  }
  const folder = join(repo, uses.slice(2));
  for (const name of ["action.yml", "action.yaml"]) {
    if (existsSync(join(folder, name))) {
      return join(folder, name);
    }
  }
  return join(folder, "action.yml"); // missing: reported by the caller
}

/** Why `job` must run inside the production environment ([] if it need not). */
export function productionReasons(job: Data, workflowText = "", repo = REPO): string[] {
  const reasons: string[] = [];
  for (const name of productionSecretNames(`${workflowText}\n${strings(job)}`)) {
    reasons.push(`reads secrets.${name}`);
  }
  if (job.secrets === "inherit") {
    reasons.push("inherits every repo-level secret (`secrets: inherit`)");
  }
  for (const each of job.steps ?? []) {
    const hit = each.run ? HOST_SHELL.exec(String(each.run)) : null;
    if (hit) {
      reasons.push(`opens a shell to a host (\`${hit[1]}\`)`);
      break;
    }
  }
  for (const each of job.steps ?? []) {
    const uses = String(each.uses ?? "");
    const action = localAction(uses, repo);
    if (action === null) {
      continue;
    }
    if (!existsSync(action)) {
      reasons.push(`uses ${uses}, which does not exist`);
      continue;
    }
    const text = readFileSync(action, "utf8");
    for (const name of productionSecretNames(text)) {
      reasons.push(`uses ${uses}, which reads secrets.${name}`);
    }
    if (HOST_SHELL.test(text)) {
      reasons.push(`uses ${uses}, which opens a shell to a host`);
    }
  }
  return reasons;
}

function environmentName(job: Data): string | null {
  const env = job.environment;
  const name = env && typeof env === "object" ? env.name : env;
  return typeof name === "string" ? name : null;
}

function workflowLevelText(data: Data): string {
  return strings({ env: data.env ?? {}, defaults: data.defaults ?? {} });
}

/** The events a workflow runs on: `on:` as a name, a list or a map. */
function events(data: Data): string[] {
  const on = data.on;
  return typeof on === "string" ? [on] : Array.isArray(on) ? on.map(String)
    : Object.keys(on ?? {});
}

/** The steps of `job` that take a pull request's own code into its run. */
export function pullRequestCodeSteps(job: Data): string[] {
  const found: string[] = [];
  for (const [index, each] of (job.steps ?? []).entries()) {
    const step = `step ${index + 1} (${each.name ?? each.uses ?? "run"})`;
    if (String(each.uses ?? "").startsWith("actions/checkout@")
        && PULL_REQUEST_CODE.test(strings({ ref: each.with?.ref, repo: each.with?.repository }))) {
      found.push(`${step} checks out the pull request's code`);
    }
    if (GH_PR_CHECKOUT.test(String(each.run ?? ""))) {
      found.push(`${step} runs \`gh pr checkout\``);
    }
  }
  return found;
}

interface ProductionJob { workflow: string; name: string; job: Data; reasons: string[] }

function productionJobs(): ProductionJob[] {
  const out: ProductionJob[] = [];
  for (const file of workflowNames()) {
    const data = workflow(file);
    for (const [name, job] of Object.entries<Data>(data.jobs ?? {})) {
      const reasons = productionReasons(job, workflowLevelText(data));
      if (reasons.length) {
        out.push({ workflow: file, name, job, reasons });
      }
    }
  }
  return out;
}

/**
 * A `uses:` job cannot declare `environment:`; the equivalent guard is that it forwards
 * nothing production-grade to a callee this guard cannot see. `secrets: inherit` is allowed
 * only toward a workflow in this repository (whose jobs the scan holds to the same rule); an
 * explicit `secrets:` map or `with:` may carry allowlisted names only.
 */
export function reusableCallerProblems(file: string, name: string, job: Data,
                                       reasons: string[], repo = REPO): string[] {
  const problems: string[] = [];
  const target = String(job.uses ?? "");
  const local = target.startsWith("./.github/workflows/");
  if (local && !existsSync(join(repo, target.slice(2)))) {
    problems.push(`${file} job \`${name}\` calls ${target}, which does not exist`);
  }
  for (const reason of reasons) {
    if (reason.startsWith("inherits")) {
      if (!local) {
        problems.push(`${file} job \`${name}\` inherits secrets into ${target}, a workflow ` +
                      "outside this repository that this guard cannot scan");
      }
    } else {
      problems.push(`${file} job \`${name}\` is a reusable-workflow call that ${reason}; a ` +
                    "`uses:` job cannot declare `environment: production`, so it may forward " +
                    "allowlisted secrets only");
    }
  }
  return problems;
}

describe("production workflows", () => {
  const jobs = productionJobs();

  it("the scan finds the known production workflows", () => {
    const seen = new Set(jobs.map((each) => each.workflow));
    expect(KNOWN_PRODUCTION_WORKFLOWS.filter((file) => !seen.has(file)),
           "drop a workflow that stopped touching production, or the scan is broken")
      .toEqual([]);
  });

  it("every production job declares the production environment", () => {
    const missing = jobs.filter((each) => !("uses" in each.job)
                                         && environmentName(each.job) !== "production")
      .map((each) => `${each.workflow} job \`${each.name}\`: ${each.reasons.join("; ")}`);
    expect(missing, "an `if:` guard is not a substitute: a branch dispatch and a pull " +
                    "request run the branch's YAML; only a harmless secret may stay outside " +
                    "an environment, with a one-line justification").toEqual([]);
  });

  it("reusable-workflow callers forward no production secrets", () => {
    expect(jobs.filter((each) => "uses" in each.job).flatMap((each) =>
      reusableCallerProblems(each.workflow, each.name, each.job, each.reasons))).toEqual([]);
  });

  // Every such job, secret or not: a fork's run there still gets a write token, and what it
  // saves to the Actions cache lands in main's scope, where production jobs restore it.
  it("a pull_request_target job runs none of the pull request's code", () => {
    const wrong: string[] = [];
    for (const file of workflowNames()) {
      const data = workflow(file);
      if (!events(data).includes("pull_request_target")) {
        continue;
      }
      for (const [name, job] of Object.entries<Data>(data.jobs ?? {})) {
        wrong.push(...pullRequestCodeSteps(job)
          .map((step) => `${file} job \`${name}\`: ${step}`));
      }
    }
    expect(wrong, "pull_request_target runs main's YAML with the secrets: never run the PR's " +
                  "code there; build it in a `pull_request` job, which holds none").toEqual([]);
  });

  it("the allowlist is in use and never names a production secret", () => {
    const used = new Set<string>();
    const texts = [
      ...workflowNames().map((file) => readFileSync(join(WORKFLOWS, file), "utf8")),
      ...actionTexts().map(([, text]) => text)];
    for (const text of texts) {
      for (const name of secretNames(text)) {
        used.add(name.toUpperCase());
      }
    }
    expect(Object.keys(HARMLESS_SECRETS).filter((name) => !used.has(name)),
           "allowlisted secrets no workflow reads any more: delete them").toEqual([]);
    expect(Object.values(HARMLESS_SECRETS).every((why) => why.trim())).toBe(true);
  });

  it("an environment's own secret is read only inside that environment", () => {
    // The review App's key is read only by a job in the `review` environment, which GitHub
    // refuses on any ref but main, so no branch's workflow can mint the App's token.
    const wrong: string[] = [];
    const seen = new Set<string>();
    for (const file of workflowNames()) {
      const data = workflow(file);
      for (const name of secretNames(workflowLevelText(data))) {
        if (name.toUpperCase() in ENVIRONMENT_SECRETS) {
          wrong.push(`${file} reads secrets.${name} for every job`);
        }
      }
      for (const [jobName, job] of Object.entries<Data>(data.jobs ?? {})) {
        for (const name of secretNames(strings(job))) {
          const wanted = ENVIRONMENT_SECRETS[name.toUpperCase()];
          if (wanted === undefined) {
            continue;
          }
          seen.add(name.toUpperCase());
          if ("uses" in job || environmentName(job) !== wanted) {
            wrong.push(`${file} job \`${jobName}\` reads secrets.${name} outside the ` +
                       `\`${wanted}\` environment`);
          }
        }
      }
    }
    for (const [folder, text] of actionTexts()) {
      for (const name of secretNames(text)) {
        if (name.toUpperCase() in ENVIRONMENT_SECRETS) {
          wrong.push(`${folder} reads secrets.${name}`);
        }
      }
    }
    expect(wrong).toEqual([]);
    expect(Object.keys(ENVIRONMENT_SECRETS).filter((name) => !seen.has(name)),
           "environment secrets no workflow reads any more: delete them").toEqual([]);
    expect(Object.keys(ENVIRONMENT_SECRETS).filter((name) => name in HARMLESS_SECRETS))
      .toEqual([]);
    expect(Object.values(ENVIRONMENT_SECRETS)).not.toContain("production");
  });
});

describe("the classifier", () => {
  const base = { "runs-on": "ubuntu-latest",
                 steps: [{ uses: "actions/checkout@v4" }, { run: "echo hello" }] };
  const job = (overrides: Data = {}): Data => ({ ...structuredClone(base), ...overrides });

  const mustFlag: Record<string, Data> = {
    dot: job({ env: { K: "${{ secrets.DO_SSH_KEY }}" } }),
    "dot-lowercase": job({ env: { K: "${{ secrets.do_ssh_key }}" } }),
    "bracket-single": job({ env: { K: "${{ secrets['DO_SSH_KEY'] }}" } }),
    "bracket-double": job({ env: { K: '${{ secrets["DO_SSH_KEY"] }}' } }),
    "bracket-bare": job({ env: { K: "${{ secrets[DO_SSH_KEY] }}" } }),
    "bracket-dynamic": job({ env: { K: "${{ secrets[format('DO_{0}', matrix.k)] }}" } }),
    "unknown-name": job({ env: { K: "${{ secrets.BRAND_NEW_SECRET }}" } }),
    tojson: job({ env: { ALL: "${{ toJSON(secrets) }}" } }),
    "step-env": job({ steps: [{ run: "x", env: { K: "${{ secrets.DIGITALOCEAN_TOKEN }}" } }] }),
    "step-with": job({ steps: [{ uses: "x/y@v1", with: { k: "${{ secrets.DO_API_TOKEN }}" } }] }),
    ssh: job({ steps: [{ run: "ssh user@host 'uptime'" }] }),
    "ssh-piped": job({ steps: [{ run: "cat f | ssh user@host 'bash -s'" }] }),
    scp: job({ steps: [{ run: "set -e\nscp a.sh user@host:/tmp/" }] }),
    rsync: job({ steps: [{ run: "rsync -a . user@host:/opt/" }] }),
    sftp: job({ steps: [{ run: "sftp user@host <<EOF\nput x\nEOF" }] }),
    inherit: job({ secrets: "inherit" }),
    // The ones a pull request's run reached before 2026-11-14 (concern 2026-07-27, P0).
    "cloudflare-token": job({ env: { T: "${{ secrets.CLOUDFLARE_API_TOKEN }}" } }),
    "merge-token": job({ env: { T: "${{ secrets.MERGE_ATTRIBUTION_TOKEN }}" } }),
    "firebase-config": job({ steps: [{ uses: "./x", with: {
      b64: "${{ secrets.ANDROID_GOOGLE_SERVICES_JSON_B64 }}" } }] }),
  };
  const mustNotFlag: Record<string, Data> = {
    "no-secrets": job(),
    "github-token": job({ env: { GH_TOKEN: "${{ secrets.GITHUB_TOKEN }}" } }),
    "allowlisted-bracket": job({ env: { T: "${{ secrets['TINYASSETS_WIKI_CANARY_TOKEN'] }}" } }),
    "python-secrets-module": job({ steps: [{ run: "python -c 'import secrets; secrets.token_hex(8)'" }] }),
    "ssh-keyscan-only": job({ steps: [{ run: "ssh-keyscan example.org >> ~/.ssh/known_hosts" }] }),
    "ssh-in-a-word": job({ steps: [{ run: 'echo "see runbook.md#do-ssh-key"' }] }),
    "secret-as-yaml-key": job({ steps: [{ run: "echo secrets: rotated" }] }),
  };
  for (const [name, each] of Object.entries(mustFlag)) {
    it(`flags ${name}`, () => expect(productionReasons(each)).not.toEqual([]));
  }
  for (const [name, each] of Object.entries(mustNotFlag)) {
    it(`passes ${name}`, () => expect(productionReasons(each)).toEqual([]));
  }

  it("sees a step that takes a pull request's code", () => {
    const checkout = (ref: Data) => job({ steps: [{ uses: "actions/checkout@v4", with: ref }] });
    for (const each of [checkout({ ref: "${{ github.event.pull_request.head.sha }}" }),
                        checkout({ ref: "refs/pull/${{ github.event.number }}/merge" }),
                        checkout({ ref: "${{ github.head_ref }}" }),
                        checkout({
                          repository: "${{ github.event.pull_request.head.repo.full_name }}" }),
                        job({ steps: [{ run: 'gh pr checkout "$PR"' }] })]) {
      expect(pullRequestCodeSteps(each)).not.toEqual([]);
    }
    for (const each of [checkout({ ref: "${{ github.event.pull_request.base.sha }}" }),
                        checkout({ ref: "${{ github.event.repository.default_branch }}" }),
                        job({ steps: [{ run: 'git fetch origin "+refs/pull/${PR}/head:pr"' }] })]) {
      expect(pullRequestCodeSteps(each)).toEqual([]);
    }
  });

  it("counts a workflow-level env for every job", () => {
    const text = workflowLevelText({ env: { H: "${{ secrets.DO_DROPLET_HOST }}" } });
    expect(productionReasons(job(), text)).not.toEqual([]);
  });

  it("counts a local action that reads a production secret", () => {
    const repo = join(REPO, "platform", "ci", "guards", "fixtures");
    const reads = job({ steps: [{ uses: "./reads-secret" }] });
    expect(productionReasons(reads, "", repo).some((r) => r.includes("reads secrets.DO_SSH_KEY")))
      .toBe(true);
    expect(productionReasons(job({ steps: [{ uses: "./reads-input" }] }), "", repo)).toEqual([]);
    expect(productionReasons(job({ steps: [{ uses: "./absent" }] }), "", repo)).not.toEqual([]);
    expect(parse(readFileSync(join(repo, "reads-secret", "action.yml"), "utf8")).runs.using)
      .toBe("composite");
  });

  it("holds reusable-workflow callers", () => {
    const check = (each: Data) => reusableCallerProblems("w", "j", each, productionReasons(each));
    expect(check(job({ uses: "./.github/workflows/ios-release.yml", secrets: "inherit" })))
      .toEqual([]);
    expect(check(job({ uses: "other/repo/.github/workflows/x.yml@main", secrets: "inherit" })))
      .not.toEqual([]);
    expect(check(job({ uses: "./.github/workflows/does-not-exist.yml", secrets: "inherit" })))
      .not.toEqual([]);
    expect(check(job({ uses: "./.github/workflows/ios-release.yml",
                       secrets: { key: "${{ secrets.DO_SSH_KEY }}" } }))).not.toEqual([]);
    expect(check(job({ uses: "./.github/workflows/ios-release.yml",
                       secrets: { t: "${{ secrets.GITHUB_TOKEN }}" } }))).toEqual([]);
  });
});
