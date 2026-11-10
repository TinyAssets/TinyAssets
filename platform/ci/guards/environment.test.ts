/**
 * Production workflows run only inside the main-only `production` environment (moved from
 * the Python suite's `tests/test_workflow_production_environment.py`, audit D6; the old
 * host's box-preflight contract stays there and leaves with that host).
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
 */
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";
import { parse } from "yaml";

import { OPTIONAL, SECRET_INPUTS } from "../../deploy/facts";
import { REPO } from "../paths";
import { type Data, workflow, workflowNames, WORKFLOWS } from "../workflows";

/** The secrets the `production` environment must own (docs/host-actions.md row). */
const PRODUCTION_SECRETS = [
  "DO_SSH_KEY", "DO_DROPLET_HOST", "DO_SSH_USER", "DIGITALOCEAN_TOKEN", "DO_API_TOKEN",
  // The canary's session and signing key repair its own account, so a branch run must
  // never see them.
  "TINYASSETS_CANARY_SESSION_KEY", "TINYASSETS_CANARY_SESSION_SEED_DEPLOY",
  "TINYASSETS_CANARY_SESSION_SEED_UPTIME", "TINYASSETS_CANARY_HMAC_KEY",
  // The platform's: every secret a Worker's config requires and the shared apps' signing
  // secrets (platform-deploy.yml's apply; the E2B key, which spends money on E2B, in
  // sandbox-template.yml's build and checks and the deploy's pair check too), stored by the
  // founder's own gh (scripts/platform_setup.py).
  ...SECRET_INPUTS, ...OPTIONAL,
];

/**
 * Secrets a job may read WITHOUT `environment: production`: a branch dispatch that reaches
 * one cannot touch the production host or the DigitalOcean account. Every entry says why;
 * an entry no workflow uses fails, so the list cannot accrete.
 */
const NON_PRODUCTION_SECRETS: Record<string, string> = {
  GITHUB_TOKEN: "per-run token GitHub mints, scoped by `permissions:`; not a stored secret",
  MERGE_ATTRIBUTION_TOKEN: "GitHub PAT that enqueues/attributes merges on github.com only",
  TINYASSETS_WIKI_CANARY_TOKEN: "bearer for the read-only `canary` service principal; the " +
    "daemon 403s anything beyond get_status / read_graph status " +
    "(docs/reference/environment-variables.md)",
  PUSHOVER_APP_TOKEN: "send-only notification credential",
  PUSHOVER_USER_KEY: "send-only notification recipient",
  CLOUDFLARE_API_TOKEN: "Cloudflare edge account (Worker + DNS): separate credential, no " +
    "host shell; platform-deploy.yml's plan job reads it outside production by design (the " +
    "plan holds no production secret), and deploy-worker.yml dry-runs on pull_request, so it " +
    "cannot sit in a main-only environment. An edge environment is its own lane.",
  CLOUDFLARE_ACCOUNT_ID: "Cloudflare account identifier, not a credential",
  CLOUDFLARE_ZONE_ID: "Cloudflare zone identifier, not a credential",
  // Store release pipelines: these sign and upload artifacts, never reach the host, and run
  // on tag/release refs that a main-only environment would refuse.
  APPLE_DISTRIBUTION_CERT_P12_B64: "iOS distribution certificate (ios-release.yml, `app-store` env)",
  APPLE_DISTRIBUTION_CERT_PASSWORD: "password for APPLE_DISTRIBUTION_CERT_P12_B64",
  APPLE_PROVISIONING_PROFILE_B64: "iOS provisioning profile",
  APP_STORE_CONNECT_API_KEY_ID: "App Store Connect API key id",
  APP_STORE_CONNECT_API_KEY_B64: "App Store Connect API private key",
  APP_STORE_CONNECT_API_ISSUER_ID: "App Store Connect issuer id",
  APP_STORE_REVIEW_USERNAME: "App Review demo account sign-in (review video)",
  APP_STORE_REVIEW_PASSWORD: "App Review demo account password",
  AWS_DEVICEFARM_ACCESS_KEY_ID: "AWS Device Farm (review video on real devices)",
  AWS_DEVICEFARM_SECRET_ACCESS_KEY: "AWS Device Farm secret",
  AWS_DEVICEFARM_PROJECT_ARN: "AWS Device Farm project identifier",
  ANDROID_DEBUG_KEYSTORE_B64: "debug keystore for the sideload APK",
  ANDROID_UPLOAD_KEYSTORE_B64: "Play upload keystore",
  ANDROID_UPLOAD_KEYSTORE_PASSWORD: "password for the Play upload keystore",
  ANDROID_UPLOAD_KEY_ALIAS: "alias inside the Play upload keystore",
  ANDROID_UPLOAD_KEY_PASSWORD: "key password inside the Play upload keystore",
  ANDROID_GOOGLE_SERVICES_JSON_B64: "Firebase client config baked into the Android build",
  TOKEN_HEX: "python `secrets.token_hex(...)` in a run script",
};

/**
 * Secrets held by a main-only environment of their own, never by `production` (whose
 * required reviewer would hold every use): a job that reads one declares that environment,
 * and no other job may read it.
 */
const ENVIRONMENT_SECRETS: Record<string, string> = {
  // The review App's key: the only identity that may post the required `review` check
  // (review.yml). Its environment deploys from main alone, with no reviewer.
  REVIEW_APP_PRIVATE_KEY: "review",
};

/** Workflows known to hold production jobs: guards the scan against matching nothing. */
const KNOWN_PRODUCTION_WORKFLOWS = [
  "apply-daemon-env.yml", "box-preflight.yml", "cloud-only-preflight.yml", "deploy-prod.yml",
  "diagnose-prod-startup.yml", "dr-drill.yml", "droplet-resize.yml", "fetch-prod-logs.yml",
  "handles-canary.yml", "install-host-services.yml", "p0-outage-triage.yml",
  "platform-deploy.yml", "restart-daemon.yml", "sandbox-template.yml", "uptime-canary.yml",
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
  return [...secretNames(text)].filter((name) => !(name.toUpperCase() in NON_PRODUCTION_SECRETS)
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
    expect(missing, "an `if: github.ref == ...` guard is not a substitute: a branch dispatch " +
                    "runs the branch's YAML; allowlist a secret that truly cannot reach the " +
                    "host with a one-line justification").toEqual([]);
  });

  it("reusable-workflow callers forward no production secrets", () => {
    expect(jobs.filter((each) => "uses" in each.job).flatMap((each) =>
      reusableCallerProblems(each.workflow, each.name, each.job, each.reasons))).toEqual([]);
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
    expect(Object.keys(NON_PRODUCTION_SECRETS).filter((name) => !used.has(name)),
           "allowlisted secrets no workflow reads any more: delete them").toEqual([]);
    expect(PRODUCTION_SECRETS.filter((name) => name in NON_PRODUCTION_SECRETS)).toEqual([]);
    expect(Object.values(NON_PRODUCTION_SECRETS).every((why) => why.trim())).toBe(true);
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
    expect(Object.keys(ENVIRONMENT_SECRETS).filter((name) => PRODUCTION_SECRETS.includes(name)
                                                             || name in NON_PRODUCTION_SECRETS))
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
  };
  const mustNotFlag: Record<string, Data> = {
    "no-secrets": job(),
    "github-token": job({ env: { GH_TOKEN: "${{ secrets.GITHUB_TOKEN }}" } }),
    "allowlisted-bracket": job({ env: { T: "${{ secrets['MERGE_ATTRIBUTION_TOKEN'] }}" } }),
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
