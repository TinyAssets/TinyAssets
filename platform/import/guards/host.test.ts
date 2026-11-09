/**
 * The import window's host (`host.py`, `import_host_research.md` "The design"), against a
 * fake DigitalOcean and a fake Cloudflare (`fake_do.py`) and the fake host's SSH
 * (`fake-host.sh`): census makes the run's Droplet as the design says (and no second one while
 * another import host is up), every other mode finds it or refuses, its host key is pinned
 * from its tag and never trusted from `ssh-keyscan`, the egress check refuses an address that
 * is not the reserved IP, clean destroys only a Droplet held to the run's name and tags, the
 * orphan guard names each host and warns past 7 days, and the account is resolved from the
 * zone. The workflow's own steps are held to that order.
 */
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import {
  chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { afterAll, describe, expect, it } from "vitest";
import { parse } from "yaml";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = join(HERE, "..", "..", "..");
const RESERVED = "203.0.113.7";
const NOW = "2026-11-09T12:00:00Z";
const BLOB = Buffer.from("the import host's own key, as the fake makes it").toString("base64");
const HOSTKEY = `ssh-ed25519 ${BLOB}`;
const HOSTKEY_HEX = createHash("sha256").update(Buffer.from(BLOB, "base64")).digest("hex");
const DEPLOY_BLOB = Buffer.from("the deploy key").toString("base64");
const DEPLOY_MD5 = createHash("md5").update(Buffer.from(DEPLOY_BLOB, "base64")).digest("hex")
  .match(/../g)!.join(":");
const IMAGES = ["debian-12-x64", "debian-13-x64", "ubuntu-24-04-x64"].map((slug) => ({
  slug, public: true, status: "available", regions: ["sfo3"],
  distribution: slug.startsWith("debian") ? "Debian" : "Ubuntu" }));

const temps: string[] = [];
afterAll(() => {
  for (const dir of temps.splice(0)) {
    rmSync(dir, { recursive: true, force: true });
  }
});

interface Droplet { id: number; name: string; tags: string[]; status: string; created_at: string }
interface State {
  droplets: Droplet[]; reserved_ips: Record<string, { region: string; droplet: number | null }>;
  keys: { id: number; fingerprint: string }[]; images: typeof IMAGES; now: string;
  fail?: string[]; calls?: { call: string; query: Record<string, string>; body: unknown }[];
  made?: Record<string, unknown>[];
}

/** A tagged import host, as census makes it, `days` old. */
function droplet(run: string, id: number, days = 0, tags?: string[]): Droplet {
  return { id, name: `tinyassets-import-${run}`, status: "active",
           created_at: new Date(Date.parse(NOW) - days * 86_400_000).toISOString(),
           tags: tags ?? ["tinyassets", "import-window", `ta-import-${run}`,
                          `ta-hostkey-${HOSTKEY_HEX}`] };
}

/** A fake account and runner: `host(args)` runs host.py against it. */
function world(start: Partial<State> = {}) {
  const dir = mkdtempSync(join(tmpdir(), "ta-import-host-"));
  temps.push(dir);
  const bin = join(dir, "bin");
  mkdirSync(bin);
  chmodSync(join(HERE, "fake-host.sh"), 0o755);
  for (const tool of ["ssh", "ssh-keyscan", "ssh-keygen"]) {
    symlinkSync(join(HERE, "fake-host.sh"), join(bin, tool));
  }
  const stateFile = join(dir, "state.json");
  const state: State = { droplets: [], reserved_ips: { [RESERVED]: { region: "sfo3", droplet: null } },
                         keys: [{ id: 41, fingerprint: DEPLOY_MD5 }], images: IMAGES, now: NOW,
                         ...start };
  writeFileSync(stateFile, JSON.stringify(state));
  const deployKey = join(dir, "do_deploy.pub");
  writeFileSync(deployKey, `ssh-ed25519 ${DEPLOY_BLOB} deploy\n`);
  const log = join(dir, "calls.log");
  writeFileSync(log, "");
  const files = { output: join(dir, "output"), known: join(dir, "known_hosts"),
                  summary: join(dir, "summary") };
  const host = (args: string[], env: Record<string, string> = {}) => {
    const result = spawnSync("python3", [join(HERE, "fake_do.py"), stateFile, ...args], {
      encoding: "utf8",
      env: { PATH: `${bin}:/usr/bin:/bin`, DO_TOKEN: "do-token-value", FAKE_LOG: log,
             IMPORT_RESERVED_IP: RESERVED, FAKE_HOSTKEY: HOSTKEY, FAKE_MARKER: "r1",
             FAKE_EGRESS: RESERVED, PYTHONDONTWRITEBYTECODE: "1", ...env } });
    return { status: result.status, out: result.stdout + result.stderr };
  };
  const now = () => JSON.parse(readFileSync(stateFile, "utf8")) as State;
  const calls = () => (now().calls ?? []).map((each) => each.call);
  const tools = () => readFileSync(log, "utf8").split("\n").filter(Boolean);
  const outputs = () => Object.fromEntries(readFileSync(files.output, "utf8").split("\n")
    .filter(Boolean).map((line) => line.split("=") as [string, string]));
  const ensure = (mode: string, run = "r1") =>
    host(["ensure", "--run", run, "--mode", mode, "--deploy-key", deployKey,
          "--output", files.output]);
  return { host, now, calls, tools, outputs, ensure, files };
}

describe("census makes the run's import host", () => {
  it("as the design says, with the reserved IP on it, and records its host key as a tag", () => {
    const w = world();
    const run = w.ensure("census");
    expect(run.status).toBe(0);
    expect(run.out).toContain("made the run's import host tinyassets-import-r1");
    const made = w.now().made!;
    expect(made).toHaveLength(1);
    const body = made[0]! as Record<string, unknown>;
    expect(body).toMatchObject({
      name: "tinyassets-import-r1", region: "sfo3", size: "s-4vcpu-8gb", image: "debian-13-x64",
      ipv6: false, ssh_keys: [41],
      tags: ["tinyassets", "import-window", "ta-import-r1", `ta-hostkey-${HOSTKEY_HEX}`] });
    // The cloud-init: the three packages, the anchor route kept across boots, then the marker.
    const userData = String(body["user_data"]);
    expect(userData.startsWith("#cloud-config\n")).toBe(true);
    const config = JSON.parse(userData.slice("#cloud-config\n".length)) as {
      packages: string[]; ssh_keys: Record<string, string>; ssh_genkeytypes: string[];
      ssh_deletekeys: boolean; write_files: { path: string; content: string }[];
      runcmd: string[][] };
    expect(config.packages).toEqual(["docker.io", "rclone", "xfsprogs", "curl"]);
    expect(config.ssh_keys).toEqual({ ed25519_private: "fake private key\n",
                                      ed25519_public: `${HOSTKEY} fake` });
    expect(config).toMatchObject({ ssh_deletekeys: true, ssh_genkeytypes: [] });
    const route = config.write_files.find((each) => each.path.endsWith("tinyassets-anchor-route"))!;
    expect(route.content).toContain("metadata/v1/interfaces/public/0/anchor_ipv4/gateway");
    expect(route.content).toContain('ip route replace default via "$gw" dev eth0');
    expect(config.write_files.map((each) => each.path)).toContain(
      "/etc/systemd/system/tinyassets-anchor-route.service");
    expect(config.runcmd).toHaveLength(1);
    const [shell, flag, script, , runId] = config.runcmd[0]!;
    expect([shell, flag, runId]).toEqual(["sh", "-c", "r1"]);
    expect(script).toMatch(/enable --now tinyassets-anchor-route\.service && printf '%s\\n' "\$1" > \/etc\/tinyassets-import-host$/);
    // The reserved IP assigned, after the Droplet is up; the outputs for the later steps.
    const calls = w.calls();
    expect(calls.indexOf(`POST /reserved_ips/${RESERVED}/actions`))
      .toBeGreaterThan(calls.indexOf("POST /droplets"));
    expect(w.now().reserved_ips[RESERVED]!.droplet).toBe(w.now().droplets[0]!.id);
    expect(w.outputs()).toEqual({ id: String(w.now().droplets[0]!.id), address: RESERVED,
                                  hostkey: HOSTKEY_HEX });
    // The host's private key is in no output and no message.
    expect(run.out + readFileSync(w.files.output, "utf8")).not.toContain("fake private key");
    // Again: the same host, found, never a second.
    expect(w.ensure("census").out).toContain("the run's import host is tinyassets-import-r1");
    expect(w.now().made).toHaveLength(1);
  });

  it("refuses while another import host is up, or the reserved IP is on another Droplet", () => {
    const other = world({ droplets: [droplet("r0", 7, 2)] });
    const run = other.ensure("census");
    expect(run.status).toBe(2);
    expect(run.out).toContain("refused: another import host is up, and the window runs one at a " +
                              "time: tinyassets-import-r0 (Droplet 7), up 2.0 days. Clean its run first");
    expect(other.calls()).not.toContain("POST /droplets");
    const held = world({ reserved_ips: { [RESERVED]: { region: "sfo3", droplet: 99 } } });
    expect(held.ensure("census").out).toContain("refused: the reserved IP is on Droplet 99");
    expect(held.calls()).not.toContain("POST /droplets");
    // A deploy key DigitalOcean does not hold, or a run id too long for a hostname.
    expect(world({ keys: [] }).ensure("census").out)
      .toContain("refused: the deploy key (DO_SSH_KEY) is not registered with DigitalOcean");
    expect(world().ensure("census", "a".repeat(46)).out)
      .toContain("refused: a run that has an import host has an id of at most 45 characters");
  });
});

describe("every other mode finds the run's host", () => {
  it("by its tag, held to its name and tags, with the reserved IP on it", () => {
    const w = world({ droplets: [droplet("r1", 5, 1)],
                      reserved_ips: { [RESERVED]: { region: "sfo3", droplet: 5 } } });
    for (const mode of ["bundle", "verify", "offsite", "clean"]) {
      expect(w.ensure(mode).status).toBe(0);
    }
    expect(w.outputs()["id"]).toBe("5");
    expect(w.calls()).not.toContain("POST /droplets");
    expect(w.calls()).not.toContain(`POST /reserved_ips/${RESERVED}/actions`);
  });

  it("refuses when the run has none (clean has nothing to do), or another stands in its tag", () => {
    const none = world();
    const bundle = none.ensure("bundle");
    expect(bundle.status).toBe(2);
    expect(bundle.out).toContain("refused: this run has no import host: run census first");
    const clean = none.ensure("clean");
    expect(clean.status).toBe(0);
    expect(clean.out).toContain("the run has no import host: nothing to clean");
    expect(none.outputs()["id"]).toBe("");
    expect(none.calls()).not.toContain("POST /droplets");
    // Two host-key tags: neither is trusted.
    const keys = world({ droplets: [droplet("r1", 5, 0, [...droplet("r1", 5).tags,
                                                          `ta-hostkey-${"0".repeat(64)}`])],
                         reserved_ips: { [RESERVED]: { region: "sfo3", droplet: 5 } } });
    expect(keys.ensure("verify").out).toContain("refused: Droplet 5 records no one host key");
    // Tagged for the run but not its host by name, or two of them.
    const named = world({ droplets: [{ ...droplet("r1", 5), name: "tinyassets-dr-drill" }] });
    expect(named.ensure("verify").out).toContain("is not its import host by name and tags");
    const two = world({ droplets: [droplet("r1", 5), droplet("r1", 6)] });
    expect(two.ensure("verify").out).toContain("refused: 2 Droplets are tagged for this run");
    // The reserved IP elsewhere: no session is opened to a host it does not lead to.
    const away = world({ droplets: [droplet("r1", 5)],
                         reserved_ips: { [RESERVED]: { region: "sfo3", droplet: 8 } } });
    expect(away.ensure("verify").out)
      .toContain("refused: the reserved IP is not on this run's import host");
  });

  it("gives clean a held host to destroy, unreached, when it lost the reserved IP or its key", () => {
    // Lost the reserved IP, or never set up (two host-key tags, or none): found by its name and
    // tags, with no address, so it is never reached; and destroy takes it.
    for (const tags of [undefined, ["tinyassets", "import-window", "ta-import-r1"],
                        [...droplet("r1", 5).tags, `ta-hostkey-${"0".repeat(64)}`]]) {
      const w = world({ droplets: [droplet("r1", 5, 0, tags)],
                        reserved_ips: { [RESERVED]: { region: "sfo3", droplet: tags ? 5 : 8 } } });
      const run = w.ensure("clean");
      expect(run.status).toBe(0);
      expect(run.out).toContain("the host is not reached, and its copy goes with it");
      expect(w.outputs()).toMatchObject({ id: "5", address: "" });
      expect(w.host(["destroy", "--run", "r1", "--id", "5"]).status).toBe(0);
      expect(w.calls().filter((call) => call.startsWith("DELETE"))).toEqual(["DELETE /droplets/5"]);
      expect(w.calls()).not.toContain(`POST /reserved_ips/${RESERVED}/actions`);
    }
    // Held, set up and with the reserved IP: reached, for its own clean first.
    const whole = world({ droplets: [droplet("r1", 5)],
                          reserved_ips: { [RESERVED]: { region: "sfo3", droplet: 5 } } });
    expect(whole.ensure("clean").status).toBe(0);
    expect(whole.outputs()).toEqual({ id: "5", address: RESERVED, hostkey: HOSTKEY_HEX });
  });

  it("gives clean nothing to destroy that is not held to the run's name and tags", () => {
    const cases = [
      { ...droplet("r1", 5), name: "tinyassets-dr-drill" },
      droplet("r1", 5, 0, ["tinyassets", "ta-import-r1", `ta-hostkey-${HOSTKEY_HEX}`]),
      droplet("r1", 5, 0, ["import-window", "ta-import-r1", `ta-hostkey-${HOSTKEY_HEX}`]),
    ];
    for (const each of cases) {
      const w = world({ droplets: [each], reserved_ips: { [RESERVED]: { region: "sfo3", droplet: 5 } } });
      const run = w.ensure("clean");
      expect(run.status).toBe(2);
      expect(run.out).toContain("is not its import host by name and tags");
      expect(existsSync(w.files.output)).toBe(false);
      expect(w.host(["destroy", "--run", "r1", "--id", "5"]).status).toBe(2);
      expect(w.calls().filter((call) => call.startsWith("DELETE"))).toEqual([]);
    }
    // Two Droplets in the run's tag: neither.
    const two = world({ droplets: [droplet("r1", 5), droplet("r1", 6)] });
    expect(two.ensure("clean").status).toBe(2);
    expect(existsSync(two.files.output)).toBe(false);
  });
});

describe("the host key", () => {
  it("is pinned from the tag, never trusted from ssh-keyscan, then the set-up is awaited", () => {
    const w = world();
    const pin = (env: Record<string, string> = {}) => w.host(
      ["pin", "--run", "r1", "--hostkey", HOSTKEY_HEX, "--known-hosts", w.files.known,
       "--identity", "/dev/null"], env);
    const run = pin();
    expect(run.status).toBe(0);
    expect(readFileSync(w.files.known, "utf8")).toBe(`${RESERVED} ${HOSTKEY}\n`);
    expect(run.out).toContain("the import host is set up for this run");
    // Every session after it checks that key strictly.
    const sessions = w.tools().filter((line) => line.startsWith("ssh "));
    expect(sessions.length).toBeGreaterThan(0);
    for (const line of sessions) {
      expect(line).toContain(`-o StrictHostKeyChecking=yes -o UserKnownHostsFile=${w.files.known} ` +
                             `root@${RESERVED}`);
    }
    // A host offering another key is refused, and nothing is pinned.
    rmSync(w.files.known);
    const other = pin({ FAKE_HOSTKEY: `ssh-ed25519 ${Buffer.from("another").toString("base64")}` });
    expect(other.status).toBe(2);
    expect(other.out).toContain("refused: the host offered no key whose SHA-256 is its tag's");
    expect(existsSync(w.files.known)).toBe(false);
    // A marker that never names the run: its set-up never finished.
    const unset = pin({ FAKE_MARKER: "r2" });
    expect(unset.status).toBe(2);
    expect(unset.out).toContain("refused: the import host's set-up did not finish");
  });
});

describe("the egress check", () => {
  const egress = (seen: string) => {
    const w = world();
    writeFileSync(w.files.known, `${RESERVED} ${HOSTKEY}\n`);
    const run = w.host(["egress", "--run", "r1", "--mode", "verify", "--known-hosts", w.files.known,
                        "--identity", "/dev/null"],
                       { FAKE_EGRESS: seen });
    return { ...run, tools: w.tools() };
  };

  it("passes when Cloudflare sees a bridge container from the reserved IP", () => {
    const run = egress(RESERVED);
    expect(run.status).toBe(0);
    expect(run.out).toContain("Cloudflare sees the import host's containers from the reserved IP");
    const session = run.tools.find((line) => line.startsWith("ssh "))!;
    expect(session).toContain("https://www.cloudflare.com/cdn-cgi/trace");
    expect(session).toContain("-o StrictHostKeyChecking=yes");
  });

  it("runs in the run's own Node, by the digest census pinned; census's first check alone " +
     "by the tag", () => {
    const command = (mode: string, env: Record<string, string> = {}) => {
      const w = world();
      writeFileSync(w.files.known, `${RESERVED} ${HOSTKEY}\n`);
      const run = w.host(["egress", "--run", "r1", "--mode", mode, "--known-hosts", w.files.known,
                          "--identity", "/dev/null"], env);
      return { ...run, session: w.tools().find((line) => line.startsWith("ssh "))! };
    };
    for (const mode of ["census", "stage", "verify", "wipe", "admit", "dlq", "offsite"]) {
      const { session } = command(mode);
      expect(session).toContain(`image="$(cat /var/tmp/ta-import-r1/image 2>/dev/null || true)"`);
      expect(session).toContain("grep -Eqx 'node@sha256:[0-9a-f]{64}'");
      expect(session).toContain('docker run --rm --network bridge "$image" node -e');
      if (mode === "census") {
        expect(session).toContain("|| { image=node:22; }");
      } else {
        expect(session).not.toContain("node:22");
        expect(session).toContain("|| { echo image=none; exit 3; }");
      }
    }
    // A later mode on a copy that names no digest: refused, never the tag.
    const none = command("verify", { FAKE_NO_IMAGE: "1" });
    expect(none.status).toBe(2);
    expect(none.out).toContain("refused: this run's copy names no Node image: run clean, then census");
  });

  it("refuses any other address, and a trace it cannot read", () => {
    for (const seen of ["198.51.100.4", `${RESERVED}0`, ""]) {
      const run = egress(seen);
      expect(run.status).toBe(2);
      expect(run.out).toMatch(/refused: (Cloudflare sees the import host's containers from another address than the reserved IP|the egress check could not read)/);
    }
    // A container that failed, even after the right line.
    const w = world();
    writeFileSync(w.files.known, `${RESERVED} ${HOSTKEY}\n`);
    const failed = w.host(["egress", "--run", "r1", "--mode", "verify", "--known-hosts", w.files.known,
                        "--identity", "/dev/null"],
                          { FAKE_EGRESS_STATUS: "1" });
    expect(failed.status).toBe(2);
    expect(failed.out).toContain("refused: the egress check could not read what Cloudflare sees");
  });

  it("refuses a host whose containers reach the metadata service (the host key's user_data)", () => {
    const w = world();
    writeFileSync(w.files.known, `${RESERVED} ${HOSTKEY}\n`);
    for (const metadata of ["reachable", ""]) {
      const run = w.host(["egress", "--run", "r1", "--mode", "verify", "--known-hosts", w.files.known,
                        "--identity", "/dev/null"],
                         { FAKE_METADATA: metadata });
      expect(run.status).toBe(2);
      expect(run.out).toContain("refused: a container on the import host reaches the metadata " +
                                "service");
    }
    // The boot script drops it for Docker's containers, every boot, before the marker.
    const run = w.ensure("census");
    expect(run.status).toBe(0);
    const config = JSON.parse(String(w.now().made![0]!["user_data"]).slice("#cloud-config\n".length)) as
      { write_files: { path: string; content: string }[] };
    const route = config.write_files.find((each) => each.path.endsWith("tinyassets-anchor-route"))!;
    expect(route.content).toContain(
      "|| iptables -I DOCKER-USER -d 169.254.169.254/32 -j DROP");
  });
});

describe("clean destroys the run's host", () => {
  it("only a Droplet held to the run's name and tags", () => {
    const w = world({ droplets: [droplet("r1", 5), droplet("r2", 6),
                                 { ...droplet("r1", 7), name: "tinyassets-dr-drill" },
                                 droplet("r1", 8, 0, ["tinyassets", "ta-import-r1"])] });
    for (const id of ["6", "7", "8"]) {
      const run = w.host(["destroy", "--run", "r1", "--id", id]);
      expect(run.status).toBe(2);
      expect(run.out).toContain(`Droplet ${id} is tagged for this run but is not its import host`);
    }
    expect(w.calls().filter((call) => call.startsWith("DELETE"))).toEqual([]);
    const run = w.host(["destroy", "--run", "r1", "--id", "5"]);
    expect(run.status).toBe(0);
    expect(run.out).toContain("the run's import host (Droplet 5) is destroyed");
    expect(w.calls().filter((call) => call.startsWith("DELETE"))).toEqual(["DELETE /droplets/5"]);
    expect(w.now().droplets.map((each) => each.id)).toEqual([6, 7, 8]);
    expect(w.host(["destroy", "--run", "r1", "--id", "05"]).status).toBe(2);
  });

  it("names the Droplet when its deletion fails", () => {
    const w = world({ droplets: [droplet("r1", 5)], fail: ["DELETE /droplets/5"] });
    const run = w.host(["destroy", "--run", "r1", "--id", "5"]);
    expect(run.status).toBe(1);
    expect(run.out).toContain("::error::the import host's deletion failed: Droplet 5 may still " +
                              "be billing; delete it by hand");
    expect(run.out).not.toContain("do-token-value");
  });
});

describe("the orphan guard", () => {
  it("names every import host and its age, and warns past 7 days", () => {
    const w = world({ droplets: [droplet("r1", 5, 0.5), droplet("old", 6, 9)] });
    const run = w.host(["hosts", "--summary", w.files.summary]);
    expect(run.status).toBe(0);
    expect(run.out).toContain("- tinyassets-import-r1 (Droplet 5), up 12.0 hours");
    expect(run.out).toContain("::warning::an import host has been up past 7 days: " +
                              "tinyassets-import-old (Droplet 6), up 9.0 days");
    expect(run.out).not.toMatch(/::warning::.*import-r1/);
    expect(readFileSync(w.files.summary, "utf8")).toContain("**Past 7 days:** tinyassets-import-old");
    expect(world().host(["hosts"]).out).toContain("import hosts up:\n- none");
  });
});

describe("the workflow's host steps", () => {
  const workflow = parse(readFileSync(join(REPO, ".github", "workflows", "import-window.yml"),
                                      "utf8")) as {
    env?: Record<string, string>;
    jobs: { window: { steps: { name?: string; id?: string; if?: string; run?: string;
                               env?: Record<string, string> }[] } } };
  const steps = workflow.jobs.window.steps;
  const at = (name: string) => steps.findIndex((each) => each.name === name);

  it("find, pin and check the host before the step, and destroy it after a clean", () => {
    const order = ["Refuse a run this dispatch may not make", "Name every import host and its age",
                   "Find or make the run's import host", "Pin its host key",
                   "Check its egress is the reserved IP",
                   "Run the step on the import host", "Destroy the run's import host"];
    const found = order.map(at);
    expect(found.every((index) => index >= 0)).toBe(true);
    expect([...found].sort((a, b) => a - b)).toEqual(found);
    const step = (name: string) => steps[at(name)]!;
    expect(step("Find or make the run's import host").run).toContain("host.py ensure");
    expect(step("Pin its host key").run).toContain("host.py pin");
    // The egress check before census and every mode that hands the host a credential.
    expect(step("Check its egress is the reserved IP").if).toContain(
      '["census","stage","verify","wipe","admit","dlq","offsite"]');
    expect(step("Check its egress is the reserved IP").run).toContain(
      'host.py egress --run "${RUN_ID}" --mode "${MODE}"');
    // Reached only with an address; destroyed after the host's own clean, or with none.
    for (const name of ["Pin its host key", "Run the step on the import host"]) {
      expect(step(name).if).toBe("steps.host.outputs.address != ''");
    }
    const destroys = (outcomes: { address: string; pin: string; run: string; mode?: string;
                                  id?: string }) => {
      const context: Record<string, string> = {
        "inputs.mode": outcomes.mode ?? "clean", "steps.host.outputs.id": outcomes.id ?? "5",
        "steps.host.outputs.address": outcomes.address, "steps.pin.outcome": outcomes.pin,
        "steps.run.outcome": outcomes.run };
      const js = step("Destroy the run's import host").if!
        .replace(/always\(\)/g, "true")
        .replace(/[a-z]+(\.[a-z_]+)+/g, (name) => JSON.stringify(context[name] ?? `?${name}`))
        .replace(/'([^']*)'/g, (_all, text: string) => JSON.stringify(text))
        .replace(/==/g, "===").replace(/!=/g, "!==");
      expect(js).not.toContain("?");
      return Function(`return (${js});`)() as boolean;
    };
    // The host's own clean ran: destroyed. Never when that clean refused.
    expect(destroys({ address: RESERVED, pin: "success", run: "success" })).toBe(true);
    expect(destroys({ address: RESERVED, pin: "success", run: "failure" })).toBe(false);
    // Set-up never finished (the pin refused), or no address (lost IP, no one key): destroyed.
    expect(destroys({ address: RESERVED, pin: "failure", run: "skipped" })).toBe(true);
    expect(destroys({ address: "", pin: "skipped", run: "skipped" })).toBe(true);
    // An earlier step failed with the host reached and pinned: kept, its clean never ran.
    expect(destroys({ address: RESERVED, pin: "success", run: "skipped" })).toBe(false);
    // No held host (ensure refused or found none), or another mode: nothing.
    expect(destroys({ address: "", pin: "skipped", run: "skipped", id: "" })).toBe(false);
    expect(destroys({ address: RESERVED, pin: "success", run: "success", mode: "verify" }))
      .toBe(false);
    expect(steps.find((each) => each.name === "Pin its host key")!.id).toBe("pin");
    expect(steps.find((each) => each.name === "Run the step on the import host")!.id).toBe("run");
    // Nothing of the production droplet, and no host key taken from a scan.
    const text = readFileSync(join(REPO, ".github", "workflows", "import-window.yml"), "utf8");
    expect(text).not.toMatch(/DO_DROPLET_HOST|DO_SSH_USER|ssh-keyscan|StrictHostKeyChecking=no/);
    expect(workflow.env).toBeUndefined();
  });
});
