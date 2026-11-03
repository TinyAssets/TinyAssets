/**
 * Drives every case of the latency probe (latency-probe.md) through the door, one request a
 * case, and writes the results: p50, p95 and p99 of each case's samples, with where each
 * object ran.
 *
 *   PROBE_KEY=... node drive.mjs --base https://ta-latencyprobe-door.<sub>.workers.dev \
 *       --run-id ID [--out results.json] [--summary results.md] [--r2 yes|no] [--n 300] \
 *       [--redact TEXT]
 *
 * The run id names every object and every key, so no run reads another's. Each case runs in
 * one Owner object, `<run>-owner`, made by the first case; option A's shards are made by that
 * object's own calls, so their placement is the owner's to decide, as it would be live.
 */
import { writeFileSync } from "node:fs";

export const SIZES = Object.freeze(["1k", "64k", "1m"]);

/** The cases, in order: each R2 read and list follows the put that wrote its keys. */
export function plan({ r2 = true, n = 300 } = {}) {
  const cases = [
    { name: "where", method: "where", body: { count: 4 } },
    { name: "A ping, sequential", method: "shard", body: { kind: "ping", n } },
    { name: "A write+read, warm, sequential", method: "shard", body: { kind: "rw", n } },
    { name: "A write+read, warm, 6-parallel", method: "shard",
      body: { kind: "rw", n, parallel: true } },
    { name: "A write+read, cold (new shard), sequential", method: "shard",
      body: { kind: "cold", n: Math.max(1, Math.round(n / 3)) } },
    { name: "A write+read, cold (new shard), 6-parallel", method: "shard",
      body: { kind: "cold", n: Math.max(6, Math.round(n / 3)), parallel: true } },
  ];
  if (!r2) {
    return cases;
  }
  for (const size of SIZES) {
    for (const parallel of [false, true]) {
      const how = parallel ? "6-parallel" : "sequential";
      const at = { size, n, parallel, path: "files" };
      cases.push(
        { name: `B put ${size} (create-only), ${how}`, method: "r2", body: { ...at, op: "put" } },
        { name: `B get ${size} cold, ${how}`, method: "r2", body: { ...at, op: "get_cold" } },
        { name: `B get ${size} warm, ${how}`, method: "r2", body: { ...at, op: "get_warm" } });
    }
  }
  const one = { size: "1k", n, parallel: false, path: "files" };
  cases.push(
    { name: "B put 1k over an existing key (precondition fails), sequential", method: "r2",
      body: { ...one, op: "exists" } },
    { name: "B list 1,000 (300 under the prefix), sequential", method: "r2",
      body: { ...one, op: "list", limit: 1000 } },
    { name: "B list 100, sequential", method: "r2", body: { ...one, op: "list", limit: 100 } },
    { name: "B list 1,000, 6-parallel", method: "r2",
      body: { ...one, op: "list", limit: 1000, parallel: true } });
  const direct = { size: "1k", n, parallel: false, path: "direct" };
  cases.push(
    { name: "B direct binding (no FILES hop): put 1k, sequential", method: "r2",
      body: { ...direct, op: "put" } },
    { name: "B direct binding: get 1k cold, sequential", method: "r2",
      body: { ...direct, op: "get_cold" } },
    { name: "B direct binding: get 1k warm, sequential", method: "r2",
      body: { ...direct, op: "get_warm" } },
    { name: "B direct binding: list 1,000, sequential", method: "r2",
      body: { ...direct, op: "list", limit: 1000 } });
  return cases;
}

/** The nearest-rank percentile of sorted samples. */
export function percentile(sorted, p) {
  if (sorted.length === 0) {
    return null;
  }
  return sorted[Math.min(sorted.length - 1, Math.max(0, Math.ceil(p / 100 * sorted.length) - 1))];
}

export function stats(samples) {
  const sorted = [...samples].sort((a, b) => a - b);
  const mean = sorted.length === 0 ? null
    : Math.round(10 * sorted.reduce((a, b) => a + b, 0) / sorted.length) / 10;
  return { n: sorted.length, p50: percentile(sorted, 50), p95: percentile(sorted, 95),
           p99: percentile(sorted, 99), min: sorted[0] ?? null, max: sorted.at(-1) ?? null,
           mean };
}

export function summarize(results) {
  const lines = ["| Case | n | p50 ms | p95 ms | p99 ms | min | max | mean | failed | wall s |",
                 "|---|---|---|---|---|---|---|---|---|---|"];
  for (const r of results.cases) {
    if (r.error) {
      lines.push(`| ${r.name} | error: ${String(r.error).replace(/\|/g, "/")} |||||||||`);
      continue;
    }
    const s = r.stats;
    lines.push(`| ${r.name} | ${s.n} | ${s.p50} | ${s.p95} | ${s.p99} | ${s.min} | ${s.max} | ` +
               `${s.mean} | ${r.failed} | ${(r.wallMs / 1000).toFixed(1)} |`);
  }
  const where = results.where;
  const head = [
    `- **run:** ${results.run}`,
    `- **door entered at:** ${where?.doorColo ?? "?"}`,
    `- **owner object at:** ${where?.owner?.colo ?? "?"} (${where?.owner?.loc ?? "?"})`,
    `- **files Worker at:** ${where?.files?.colo ?? "none (no bucket)"}`,
    `- **shards made by the owner at:** ${(where?.shards ?? []).map((s) => s.colo).join(", ") || "?"}`,
    `- **bucket:** ${results.bucket ?? "none"}`,
  ];
  return [...head, "", ...lines].join("\n");
}

async function main(argv) {
  const at = (flag, fallback = null) => {
    const i = argv.indexOf(flag);
    return i < 0 ? fallback : argv[i + 1] ?? fallback;
  };
  const base = at("--base");
  const run = at("--run-id");
  const key = process.env.PROBE_KEY;
  if (!base || !run || !key) {
    throw new Error("usage: PROBE_KEY=... drive.mjs --base URL --run-id ID [--out F] " +
                    "[--summary F] [--r2 yes|no] [--n N] [--bucket NAME] [--redact TEXT]");
  }
  const r2 = at("--r2", "yes") === "yes";
  const n = Number(at("--n", "300"));
  const redact = at("--redact");
  const object = `${run}-owner`.replace(/[^\w.-]/g, "-").slice(0, 64);
  const runId = run.replace(/[^\w.-]/g, "-").slice(0, 64);
  const call = async (method, body) => {
    const response = await fetch(`${base}/call/${object}/${method}`, {
      method: "POST", body: JSON.stringify(body), signal: AbortSignal.timeout(15 * 60_000),
      headers: { "x-probe-key": key, "x-probe-run": runId, "content-type": "application/json" },
    });
    const answer = await response.json().catch(() => ({ error: `HTTP ${response.status}` }));
    if (!response.ok || answer.error) {
      throw new Error(answer.error ?? `HTTP ${response.status}`);
    }
    return answer;
  };
  const results = { run: runId, bucket: at("--bucket"), where: null, cases: [] };
  const write = () => {
    let text = JSON.stringify(results, null, 2);
    let md = summarize(results);
    if (redact) {
      text = text.replaceAll(redact, "<redacted>");
      md = md.replaceAll(redact, "<redacted>");
    }
    if (at("--out")) {
      writeFileSync(at("--out"), text);
    }
    if (at("--summary")) {
      writeFileSync(at("--summary"), md);
    }
    return md;
  };
  for (const step of plan({ r2, n })) {
    const started = Date.now();
    try {
      const { result, doorColo } = await call(step.method, step.body);
      if (step.method === "where") {
        results.where = { ...result, doorColo };
        console.log(`where: door ${doorColo}, owner ${result.owner?.colo}, files ` +
                    `${result.files?.colo ?? "-"}, shards ${result.shards.map((s) => s.colo)}`);
        continue;
      }
      results.cases.push({ name: step.name, body: step.body, stats: stats(result.samples),
                           samples: result.samples, failed: result.failed,
                           errors: result.errors, wallMs: result.wallMs, doorColo });
      const s = results.cases.at(-1).stats;
      console.log(`${step.name}: p50 ${s.p50} p95 ${s.p95} p99 ${s.p99} (n ${s.n}, ` +
                  `failed ${result.failed}, ${((Date.now() - started) / 1000).toFixed(1)} s)`);
    } catch (error) {
      results.cases.push({ name: step.name, body: step.body, error: String(error).slice(0, 300) });
      console.log(`${step.name}: ERROR ${String(error).slice(0, 300)}`);
    }
    write();
  }
  if (r2) {
    try {
      const { result } = await call("cleanup", {});
      results.cleanup = result;
      console.log(`objects deleted: ${result.deleted}; objects left in the bucket: ${result.left}`);
    } catch (error) {
      results.cleanup = { error: String(error).slice(0, 300) };
      console.log(`cleanup: ERROR ${String(error).slice(0, 300)}`);
    }
  }
  const md = write();
  console.log("\n" + md);
  console.log("\nRESULTS-JSON-BEGIN");
  console.log(JSON.stringify(results.cases.map(({ samples: _samples, ...rest }) => rest)));
  console.log("RESULTS-JSON-END");
  if (r2 && (results.cleanup?.error || results.cleanup?.left !== 0)) {
    process.exitCode = 1;
  }
}

if (process.argv[1] && import.meta.url === `file://${process.argv[1]}`) {
  await main(process.argv.slice(2));
}
