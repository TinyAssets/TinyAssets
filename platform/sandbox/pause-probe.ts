/**
 * The long-pause probe (`e2b-pause-probe.yml`; `tasks.md` §10 step 2): how E2B resumes a
 * sandbox of the pinned template after days paused, which sets how often an owner signs in
 * again. Two sandboxes, `small` and `scratch` (about 5 GB of scratch), each holding a known
 * file, its SHA-256 and a long-lived process, paused once by `start`; `check` resumes each,
 * times the resume to its first byte, verifies what it held and pauses it again; `cleanup`
 * kills them all. It speaks E2B through the sandbox Worker's own client (`src/e2b.ts`).
 *
 *   node --experimental-strip-types --no-warnings --import ./platform/sandbox/template/resolve.mjs
 *     platform/sandbox/pause-probe.ts start|check|cleanup [--ids "<id> ..."] [--summary <file>]
 *
 * E2B's key is `E2B_API_KEY`. Every E2B answer outside what is expected fails the run, with
 * its reason in the summary.
 */
import { createHash, randomBytes } from "node:crypto";
import { appendFileSync, realpathSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { type E2b, type E2bNetwork, type Handle, runtimeE2b } from "./src/e2b";
import { pinned } from "./template/checks";

/** The probe's sandboxes carry `ta_probe=long-pause`, and nothing else does. */
const PROBE = { key: "ta_probe", value: "long-pause" } as const;
/** The two sandboxes, by their `ta_label`: one with scratch written, one without. */
const SANDBOXES = [{ label: "small", scratched: false },
                   { label: "scratch", scratched: true }] as const;

const DIR = "/var/tmp/ta-probe";
const KNOWN = `${DIR}/known.bin`;
const SCRATCH = `${DIR}/scratch.bin`;
const RECORD = `${DIR}/record.json`;
const PAUSED_AT = `${DIR}/paused_at`;
const PID = `${DIR}/pid`;
const TICK = `${DIR}/tick`;
export const TICKER = `${DIR}/ticker.sh`;

const GIB = 2 ** 30;
/** About 5 GB of scratch, leaving the disk this much free (envd, logs, root's reserve). */
export const SCRATCH_TARGET = 5 * GIB;
export const HEADROOM = 2 * GIB;
/** Below this the scratch sandbox measures nothing the small one does not. */
export const SCRATCH_FLOOR = GIB;

/** Its lifetime while it is set up, and once resumed: E2B pauses it, with memory, at its end. */
const SETUP_S = 1800;
const RESUMED_S = 900;
const COMMAND_MS = 1_200_000;

/** No way out: the probe needs none. */
const CLOSED: E2bNetwork = { allowOut: [], denyOut: ["0.0.0.0/0"], dnsServers: ["1.1.1.1"],
                             rules: {} };

/** What `start` recorded in the sandbox, which `check` verifies. */
interface Held {
  readonly known_sha256: string;
  readonly pid: number;
  readonly scratch: { readonly bytes: number; readonly sha256: string } | null;
}

/** The ticker: its pid in `pid`, then a count every 30 s, until it is killed. */
const TICKER_SH = `echo $$ > ${PID}
n=0
while :; do n=$((n+1)); echo "$n $(date -u +%FT%TZ)" > ${TICK}; sleep 30; done
`;

/** What the sandbox holds now, one `key=value` a line (`facts`). */
const INSPECT = `echo "record=$(cat ${RECORD} 2>/dev/null)"
echo "paused_at=$(cat ${PAUSED_AT} 2>/dev/null)"
echo "known_sha256=$(sha256sum ${KNOWN} 2>/dev/null | cut -d' ' -f1)"
pid=$(cat ${PID} 2>/dev/null)
echo "pid=$pid"
case "$pid" in
  ''|*[!0-9]*) ;;
  *) echo "pid_state=$(sed 's/.*) //' "/proc/$pid/stat" 2>/dev/null | cut -d' ' -f1)"
     echo "pid_cmd=$(tr '\\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null)" ;;
esac
echo "tick=$(cat ${TICK} 2>/dev/null)"
if [ -e ${SCRATCH} ]; then
  echo "scratch_bytes=$(stat -c %s ${SCRATCH})"
  echo "scratch_sha256=$(sha256sum ${SCRATCH} | cut -d' ' -f1)"
fi
`;

type Facts = Readonly<Record<string, string>>;

/** The inspection's `key=value` lines; a value keeps any `=` after the first. */
export function facts(stdout: string): Facts {
  const out: Record<string, string> = {};
  for (const line of stdout.split("\n")) {
    const at = line.indexOf("=");
    if (at > 0) {
      out[line.slice(0, at)] = line.slice(at + 1);
    }
  }
  return out;
}

function held(text: string | undefined): Held | null {
  const sha = (each: unknown) => typeof each === "string" && /^[0-9a-f]{64}$/.test(each);
  try {
    const value = JSON.parse(text ?? "") as Held;
    const scratch = value.scratch === null
      || (Number.isInteger(value.scratch.bytes) && sha(value.scratch.sha256));
    return sha(value.known_sha256) && Number.isInteger(value.pid) && value.pid > 0 && scratch
      ? value : null;
  } catch {
    return null;
  }
}

/**
 * Each way the sandbox no longer holds what `start` left in it: the known file's SHA-256,
 * the ticker alive at its pid (running or asleep, not a zombie, and still the ticker), and the
 * scratch's size and SHA-256. None: it held all of it.
 */
export function verdict(seen: Facts): string[] {
  const record = held(seen["record"]);
  if (record === null) {
    return [`the probe's record (${RECORD}) is missing or unreadable`];
  }
  const out: string[] = [];
  const known = seen["known_sha256"] ?? "";
  if (known !== record.known_sha256) {
    out.push(`the known file's SHA-256 is ${known || "missing"}, not ${record.known_sha256}`);
  }
  const state = seen["pid_state"] ?? "";
  if (seen["pid"] !== String(record.pid)) {
    out.push(`the pid file says ${seen["pid"] || "nothing"}, not ${record.pid}`);
  } else if (!/^[RSD]$/.test(state)) {
    out.push(`the process ${record.pid} is ${state === "" ? "gone" : `in state ${state}`}`);
  } else if (!(seen["pid_cmd"] ?? "").includes(TICKER)) {
    out.push(`the pid ${record.pid} is another process: ${seen["pid_cmd"] ?? ""}`);
  }
  if (record.scratch !== null) {
    const bytes = seen["scratch_bytes"];
    const sha = seen["scratch_sha256"] ?? "";
    if (bytes !== String(record.scratch.bytes)) {
      out.push(`the scratch is ${bytes === undefined ? "missing" : `${bytes} B`}, not ${
        record.scratch.bytes} B`);
    } else if (sha !== record.scratch.sha256) {
      out.push(`the scratch's SHA-256 is ${sha}, not ${record.scratch.sha256}`);
    }
  }
  return out;
}

/**
 * The scratch to write, given the disk's free bytes: the target, or less (whole MiB) when the
 * disk would keep under `HEADROOM` free, said in `note`. Under `SCRATCH_FLOOR` it throws.
 */
export function scratchBytes(free: number): { bytes: number; note: string | null } {
  const room = Math.floor((free - HEADROOM) / 2 ** 20) * 2 ** 20;
  if (room >= SCRATCH_TARGET) {
    return { bytes: SCRATCH_TARGET, note: null };
  }
  if (!(room >= SCRATCH_FLOOR)) {
    throw new Error(`the template's disk has ${free} B free: under ${
      SCRATCH_FLOOR + HEADROOM} B, too little for a scratch resume`);
  }
  return { bytes: room, note: `less than the ${SCRATCH_TARGET} B asked: the disk had ${free} ` +
                              `B free and keeps ${HEADROOM} B` };
}

function span(ms: number): string {
  return `${(ms / 3_600_000).toFixed(1)} h (${(ms / 86_400_000).toFixed(2)} d)`;
}

function reason(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** The summary's lines, written to the job summary and the log as they come. */
class Summary {
  readonly #file: string | null;

  constructor(file: string | null) {
    this.#file = file;
  }

  line(text = ""): void {
    process.stdout.write(`${text}\n`);
    if (this.#file !== null) {
      appendFileSync(this.#file, `${text}\n`);
    }
  }
}

async function sh(e2b: E2b, box: Handle, script: string): Promise<string> {
  const done = await e2b.exec(box, ["/bin/sh", "-c", script],
                              { timeoutMs: COMMAND_MS, keepalive: true });
  if (done.exitCode !== 0) {
    throw new Error(`a command in ${box.sandboxID} exited ${done.exitCode}: ${
      done.stderr.trim().slice(0, 500)}`);
  }
  return done.stdout;
}

async function inspect(e2b: E2b, box: Handle): Promise<{ seen: Facts; ms: number }> {
  const started = Date.now();
  const seen = facts(await sh(e2b, box, INSPECT));
  return { seen, ms: Date.now() - started };
}

/** Its pause time written in it, then paused with its memory, as E2B then lists it. */
async function pause(e2b: E2b, box: Handle): Promise<string> {
  await e2b.upload(box, PAUSED_AT, new TextEncoder().encode(new Date().toISOString()));
  const started = Date.now();
  const answer = await e2b.pause(box.sandboxID);
  const ms = Date.now() - started;
  if (answer !== "paused") {
    throw new Error(`E2B's pause of ${box.sandboxID} answered ${answer}${answer === "busy"
      ? `: it pauses itself at its timeout, ${RESUMED_S} s after its resume` : ""}`);
  }
  const listed = await e2b.info(box.sandboxID);
  if (listed?.state !== "paused") {
    throw new Error(`${box.sandboxID} is ${listed?.state ?? "gone"} after its pause`);
  }
  return `${ms} ms, ${listed.pauseMode ?? "mode not given"}`;
}

/** One sandbox made and filled, its record written and verified, then paused: its row. */
async function startOne(e2b: E2b, template: string, started: string,
                        { label, scratched }: (typeof SANDBOXES)[number], made: string[]):
    Promise<string> {
  const box = await e2b.create({ template, timeoutS: SETUP_S, network: CLOSED,
                                 metadata: { [PROBE.key]: PROBE.value, ta_label: label,
                                             ta_started: started } });
  made.push(box.sandboxID);
  await e2b.upload(box, TICKER, new TextEncoder().encode(TICKER_SH));
  await sh(e2b, box, `setsid /bin/sh ${TICKER} </dev/null >/dev/null 2>&1 &`);
  const known = randomBytes(2 ** 20);
  const knownSha = createHash("sha256").update(known).digest("hex");
  await e2b.upload(box, KNOWN, known);
  let scratch: Held["scratch"] = null;
  let written = "none";
  if (scratched) {
    const free = Number((await sh(e2b, box, `df -B1 --output=avail ${DIR} | tail -1`)).trim());
    const size = scratchBytes(free);
    const at = Date.now();
    const sha = (await sh(e2b, box, `head -c ${size.bytes} /dev/urandom | tee ${SCRATCH} | ` +
                                    "sha256sum | cut -d' ' -f1 && sync")).trim();
    scratch = { bytes: size.bytes, sha256: sha };
    written = `${size.bytes} B in ${Date.now() - at} ms${size.note === null ? ""
      : ` (${size.note})`}`;
  }
  const pid = Number((await sh(e2b, box, `cat ${PID}`)).trim());
  if (!Number.isInteger(pid) || pid <= 0) {
    throw new Error(`${box.sandboxID} (${label}): the ticker wrote no pid`);
  }
  const record: Held = { known_sha256: knownSha, pid, scratch };
  await e2b.upload(box, RECORD, new TextEncoder().encode(JSON.stringify(record)));
  const failures = verdict((await inspect(e2b, box)).seen);
  if (failures.length > 0) {
    throw new Error(`${box.sandboxID} (${label}) failed its own check before the pause: ${
      failures.join("; ")}`);
  }
  return `| \`${box.sandboxID}\` | ${label} | ${knownSha.slice(0, 16)}… | ${pid} | ${written} | ${
    await pause(e2b, box)} |`;
}

async function start(e2b: E2b, summary: Summary): Promise<void> {
  const already = await e2b.list(PROBE.key, PROBE.value);
  if (already.length > 0) {
    throw new Error(`${already.length} long-pause sandboxes exist already (${
      already.join(", ")}): check them, or run cleanup first`);
  }
  const template = pinned();
  const started = new Date().toISOString();
  summary.line(`Started **${started}** from \`${template}\`.`);
  summary.line();
  summary.line("| sandbox | label | known file | pid | scratch | paused |");
  summary.line("|---|---|---|---|---|---|");
  const made: string[] = [];
  try {
    for (const each of SANDBOXES) {
      summary.line(await startOne(e2b, template, started, each, made));
    }
  } catch (error) {
    const unkilled: string[] = [];
    for (const id of made) {
      await e2b.kill(id).catch((failed: unknown) => unkilled.push(`${id} (${reason(failed)})`));
    }
    throw new Error(`${reason(error)}. Killed what this run made (${made.join(", ") || "none"})${
      unkilled.length === 0 ? "" : `, but not ${unkilled.join(", ")}`}`);
  }
  summary.line();
  summary.line(`Check with mode \`check\` at 14 and 30 days: ids \`${made.join(" ")}\`, or none ` +
               `to find them by \`${PROBE.key}=${PROBE.value}\`.`);
}

/**
 * One sandbox resumed, timed and verified, then paused again: its row's cells, its failures.
 * A sandbox without the probe's tag is refused before any resume, whoever passed its id.
 */
export async function checkOne(e2b: E2b, id: string):
    Promise<{ cells: string[]; failures: string[] }> {
  const listed = await e2b.info(id);
  if (listed === null) {
    return { cells: [], failures: ["E2B holds no such sandbox: dropped while paused, or killed"] };
  }
  const cells = [listed.metadata["ta_label"] ?? "?"];
  if (listed.metadata[PROBE.key] !== PROBE.value) {
    return { cells, failures: ["not a long-pause probe sandbox: refused, not resumed"] };
  }
  if (listed.state !== "paused") {
    return { cells, failures: [`${listed.state}, not paused: no resume to measure`] };
  }
  const started = listed.metadata["ta_started"];
  const resumed = Date.now();
  const box = await e2b.connect(id, RESUMED_S);
  const connected = Date.now() - resumed;
  if (box === null) {
    return { cells, failures: ["E2B lost it at its resume"] };
  }
  const failures: string[] = [];
  try {
    await sh(e2b, box, "echo ok");
    const firstByte = Date.now() - resumed;
    const { seen, ms } = await inspect(e2b, box);
    failures.push(...verdict(seen));
    const since = (iso: string | undefined) => iso ? span(resumed - Date.parse(iso)) : "?";
    cells.push(since(started), since(seen["paused_at"]), `${connected} ms`, `${firstByte} ms`,
               `${ms} ms`, seen["tick"] || "?");
  } catch (error) {
    failures.push(reason(error));  // its cells left blank, and it is paused again all the same
    cells.push(...Array<string>(6).fill(""));
  }
  try {
    cells.push(await pause(e2b, box));
  } catch (error) {
    failures.push(reason(error));
  }
  return { cells, failures };
}

async function check(e2b: E2b, summary: Summary, ids: readonly string[]): Promise<void> {
  const boxes = ids.length > 0 ? ids : await e2b.list(PROBE.key, PROBE.value);
  if (boxes.length === 0) {
    throw new Error("no long-pause sandbox to check: run start first");
  }
  const columns = ["sandbox", "label", "since start", "paused for", "connect", "first byte",
                   "verify", "tick", "paused again", "result"];
  summary.line(`| ${columns.join(" | ")} |`);
  summary.line(`|${"---|".repeat(columns.length)}`);
  const failed: string[] = [];
  for (const id of boxes) {
    let ran: { cells: string[]; failures: string[] };
    try {
      ran = await checkOne(e2b, id);
    } catch (error) {
      ran = { cells: [], failures: [reason(error)] };
    }
    const cells = [`\`${id}\``, ...ran.cells];
    while (cells.length < columns.length - 1) {
      cells.push("");
    }
    summary.line(`| ${[...cells, ran.failures.length === 0 ? "held" : "**failed**"].join(" | ")} |`);
    failed.push(...ran.failures.map((why) => `${id}: ${why}`));
  }
  if (failed.length > 0) {
    throw new Error(failed.join("; "));
  }
  summary.line();
  summary.line("Every sandbox held its file, its process and its scratch, and is paused again.");
}

async function cleanup(e2b: E2b, summary: Summary): Promise<void> {
  const boxes = await e2b.list(PROBE.key, PROBE.value);
  for (const id of boxes) {
    await e2b.kill(id);
  }
  const left = await e2b.list(PROBE.key, PROBE.value);
  summary.line(`Killed ${boxes.length} (${boxes.join(", ") || "none"}); E2B lists ${
    left.length} left with \`${PROBE.key}=${PROBE.value}\`, running or paused.`);
  if (left.length > 0) {
    throw new Error(`still listed after the kill: ${left.join(", ")}`);
  }
}

const USAGE = "pause-probe.ts start|check|cleanup [--ids <ids>] [--summary <file>]";

async function main(argv: readonly string[]): Promise<number> {
  const [mode, ...rest] = argv;
  const options = new Map<string, string>();
  for (let i = 0; i < rest.length; i += 2) {
    const [name, value] = [rest[i]!, rest[i + 1]];
    if ((name !== "--ids" && name !== "--summary") || value === undefined) {
      throw new Error(USAGE);
    }
    options.set(name, value);
  }
  if (mode !== "start" && mode !== "check" && mode !== "cleanup") {
    throw new Error(USAGE);
  }
  const ids = (options.get("--ids") ?? "").split(/[\s,]+/).filter((id) => id !== "");
  if (ids.length > 0 && mode !== "check") {
    throw new Error("ids are for check alone: start makes its own, and cleanup kills every " +
                    "long-pause sandbox");
  }
  const summary = new Summary(options.get("--summary") ?? null);
  summary.line(`### E2B long-pause probe: ${mode}`);
  summary.line();
  try {
    const e2b = runtimeE2b(process.env["E2B_API_KEY"] ?? "");
    await (mode === "start" ? start(e2b, summary) : mode === "check" ? check(e2b, summary, ids)
      : cleanup(e2b, summary));
    return 0;
  } catch (error) {
    summary.line();
    summary.line(`**Failed:** ${reason(error)}`);
    return 1;
  }
}

if (process.argv[1] !== undefined
    && realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2)).then((code) => process.exit(code), (error: unknown) => {
    process.stderr.write(`pause-probe: ${reason(error)}\n`);
    process.exit(1);
  });
}
