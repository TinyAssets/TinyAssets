/**
 * The scratch probe (the scratch-versus-storage design, 2026-11-03): one throwaway E2B sandbox
 * from the versioned template, driven through `platform/sandbox/src/e2b.ts` (the sandbox
 * Worker's own client), then killed and proven gone. It measures:
 *   1. free space and inode headroom on the template's disk, its filesystem and memory;
 *   2. resume time with several GB of scratch written (pause with memory, a wait, connect,
 *      the first command);
 *   3. a full disk: what a write meets (ENOSPC), whether envd still answers, and that freeing
 *      space recovers it.
 * Under 30 sandbox-minutes by design (a timeout of 20 minutes on the sandbox itself).
 *
 *   E2B_API_KEY=... node scripts/probes/sandboxprobe/probe.mjs [--scratch-gb 6] [--out F]
 */
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const HERE = dirname(fileURLToPath(import.meta.url));
const PLATFORM = join(HERE, "..", "..", "..", "platform");
const require = createRequire(join(PLATFORM, "package.json"));
const ts = require("typescript");

/** The sandbox Worker's own E2B client, transpiled (it imports nothing). */
async function client() {
  const source = readFileSync(join(PLATFORM, "sandbox", "src", "e2b.ts"), "utf8");
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext,
                                                             target: ts.ScriptTarget.ES2022 } })
    .outputText;
  return import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);
}

function template() {
  const text = readFileSync(join(PLATFORM, "sandbox", "wrangler.jsonc"), "utf8");
  return /"E2B_TEMPLATE":\s*"([^"]+)"/.exec(text)[1];
}

const args = process.argv.slice(2);
const flag = (name, fallback) => {
  const i = args.indexOf(name);
  return i < 0 ? fallback : args[i + 1];
};
const scratchGb = Number(flag("--scratch-gb", "6"));
const out = flag("--out", null);
const key = process.env.E2B_API_KEY;
if (!key) {
  throw new Error("E2B_API_KEY is not set");
}
const { E2b } = await client();
const e2b = new E2b(key);
const results = { template: template(), scratch_gb: scratchGb, steps: {} };
const save = () => {
  if (out) {
    writeFileSync(out, JSON.stringify(results, null, 2));
  }
};
const tag = `scratchprobe-${Date.now()}`;
let handle = null;
const sh = async (script, timeoutMs = 600_000) => {
  const started = Date.now();
  const done = await e2b.exec(handle, ["/bin/sh", "-c", script], { timeoutMs });
  return { ...done, ms: Date.now() - started };
};
try {
  let started = Date.now();
  handle = await e2b.create({ template: results.template, timeoutS: 1200,
                              network: { allowOut: [], denyOut: ["0.0.0.0/0"],
                                         dnsServers: ["1.1.1.1"], rules: {} },
                              metadata: { ta_probe: tag } });
  results.steps.create_ms = Date.now() - started;
  console.log(`created ${handle.sandboxID} in ${results.steps.create_ms} ms`);

  // 1. The disk and the machine.
  results.steps.disk = await sh("df -B1 --output=size,used,avail,target / ; df " +
    "--output=itotal,iused,iavail / ; findmnt -no SOURCE,FSTYPE,OPTIONS / ; " +
    "tune2fs -l \"$(findmnt -no SOURCE /)\" 2>/dev/null | grep -E 'Block size|Inode count|" +
    "Inode size|Reserved block count' ; nproc ; free -b | head -2");
  console.log(results.steps.disk.stdout);
  save();

  // 2. Scratch written: GB of random bytes and a node_modules-sized tree of small files.
  started = Date.now();
  results.steps.fill = await sh(`mkdir -p /scratch/probe/tree && cd /scratch/probe && ` +
    `for i in $(seq 1 ${scratchGb}); do head -c 1073741824 /dev/urandom > big$i || exit 1; ` +
    "done; i=0; while [ $i -lt 20000 ]; do echo \"module $i\" > tree/m$i.js; i=$((i+1)); " +
    "done; sync; df -B1 --output=used,avail / ; df --output=iused,iavail /", 900_000);
  console.log(`scratch written in ${Date.now() - started} ms:`, results.steps.fill.stdout,
              results.steps.fill.stderr);
  save();

  // Resume with that disk: pause with memory, wait, connect, first command.
  started = Date.now();
  results.steps.pause = { answer: await e2b.pause(handle.sandboxID), ms: Date.now() - started };
  console.log("pause:", results.steps.pause);
  await new Promise((resolve) => setTimeout(resolve, 60_000));
  results.steps.paused_info = await e2b.info(handle.sandboxID);
  started = Date.now();
  handle = await e2b.connect(handle.sandboxID, 1200);
  const connected = Date.now() - started;
  const first = await sh("cat /scratch/probe/tree/m19999.js; df -B1 --output=used /");
  results.steps.resume = { connect_ms: connected, first_command_ms: first.ms,
                           total_ms: Date.now() - started, out: first.stdout };
  console.log("resume:", results.steps.resume);
  save();

  // 3. A full disk: what a write meets, whether envd answers, and recovery.
  started = Date.now();
  results.steps.full = await sh("dd if=/dev/zero of=/scratch/probe/fill bs=4M status=none; " +
    "echo dd_exit=$?; echo state > /var/tmp/after-full 2>&1; echo write_exit=$?; " +
    "df -B1 --output=avail /", 900_000);
  results.steps.full.ms = Date.now() - started;
  console.log("full:", results.steps.full);
  results.steps.envd_while_full = await sh("echo alive; df -B1 --output=avail /");
  results.steps.recover = await sh("rm -f /scratch/probe/fill; echo state > /var/tmp/after " +
    "&& echo write_ok; df -B1 --output=avail /");
  console.log("while full:", results.steps.envd_while_full, "recovered:", results.steps.recover);
  save();
} catch (error) {
  results.error = String(error);
  console.error(error);
} finally {
  if (handle !== null) {
    await e2b.kill(handle.sandboxID).catch((error) => {
      results.kill_error = String(error);
    });
    results.gone = (await e2b.info(handle.sandboxID)) === null;
  }
  results.left = await e2b.list("ta_probe", tag).catch((error) => [`list failed: ${error}`]);
  console.log(`killed: ${results.gone}; left with the probe's tag: ${JSON.stringify(results.left)}`);
  save();
}
