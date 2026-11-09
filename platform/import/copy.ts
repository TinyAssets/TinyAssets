/**
 * The backup copy's own checks (`import_research.md` §1, the census commands; tasks.md §9
 * item 7), the job's side of the copy that the window's host script (`window.sh`) makes on the
 * droplet. The host has no Node (`deploy/hetzner-bootstrap.sh` installs Python, rclone, the
 * util-linux tools and Docker), so the host script does what needs the host: the backup
 * remote's `rclone`, the free-disk refusal (`df`), the decompression and the read-only loop
 * mounts. It runs these in the job's `node:22` container, the copy bind-mounted read-only:
 *
 * - `copy plan <work> --name <manifest>`: the newest manifest, held to the old stack's shape
 *   (`manifestOf`) and to its own name's stamp, as `platform_backup.validate_manifest` holds
 *   it: what the host fetches, a line an object (`archive <name>`, `image <uid> <snapshot>`);
 * - `copy digests <work>`: the archive's SHA-256 against the manifest's, and each image's,
 *   uncompressed, against its pin (`dr-owner-restore.py`'s rule), each refused by its ordinal;
 * - `copy stores <work>`: every SQLite store of the copy (the shared layer's, and each image's
 *   where the kernel says it is mounted read-only from its own image) through SQLite's own
 *   `integrity_check`, opened read-only and immutable, a link never followed;
 * - `copy release <work> <file>...`: the job's output let out only when it names no owner of
 *   the copy (a principal, or its key, `sha256(principal)[:16]`, which a rehearsal's name and
 *   every digest of it hold) and no path of the copy; else withheld, kept 0600 on the host.
 *   An identities store it cannot read refuses the release whole. It is a list of exact
 *   strings, a backstop to the job's own discipline, not a proof: a principal the output
 *   escapes (JSON's `\"` or `\t`) or a path within an image, relative, is no string it holds.
 *   The job prints neither: counts, ordinals and fixed words alone.
 *
 * Stdout holds counts and ordinals alone. Deleted at the cutover with the rest of the job.
 */
import { existsSync, lstatSync, readdirSync, readFileSync, realpathSync } from "node:fs";
import { join } from "node:path";

import { sha256Hex } from "../contract/src/digest";

import {
  fileSha256, intact, isStore, layout, type Manifest, manifestOf, openStore,
} from "./backup";
import type { Copy } from "./survey";

const STAMP = "\\d{4}-\\d{2}-\\d{2}T\\d{2}-\\d{2}-\\d{2}Z";
const MANIFEST_NAME = new RegExp(`^tinyassets-platform-(${STAMP})\\.json$`);

/** The manifest at `work`, held to its own name's stamp, as the old stack's catalog holds it. */
export function namedManifest(work: string, name: string): Manifest {
  const stamp = MANIFEST_NAME.exec(name)?.[1];
  const manifest = manifestOf(readFileSync(layout(work).manifest, "utf8"));
  if (stamp === undefined || manifest.archive !== `tinyassets-data-${stamp}.tar.gz`) {
    throw new Error("the manifest's archive is not its own name's");
  }
  return manifest;
}

/** What the host fetches for the copy: the archive, then each pinned image's snapshot. */
export function copyPlan(manifest: Manifest): string[] {
  return [`archive ${manifest.archive}`,
          ...manifest.snapshots.map((pin) => `image ${pin.uid} ${pin.snapshot_id}`)];
}

/** The archive and every image held to the manifest's digests, or refused by ordinal. */
export function checkDigests(work: string, manifest: Manifest): string {
  const where = layout(work);
  if (fileSha256(where.archive(manifest.archive)) !== manifest.sha256) {
    throw new Error("the archive's SHA-256 is not the manifest's");
  }
  const total = manifest.snapshots.length;
  for (const [n, pin] of manifest.snapshots.entries()) {
    if (fileSha256(where.image(pin.uid)) !== pin.sha256) {
      throw new Error(`image ${n + 1} of ${total}'s SHA-256 is not its pin`);
    }
  }
  return `the archive and ${total} images match the manifest's digests`;
}

/** What the stores check found: counts alone. */
export interface StoresCheck {
  readonly stores: number;
  readonly intact: number;
  readonly not_intact: number;
  /** Images not mounted read-only from themselves, so not read (the census counts them). */
  readonly images_unread: number;
}

/** Every regular file under `dir` whose bytes start as a store's, by `lstat`, links unfollowed. */
function storesUnder(dir: Buffer, device: number, found: Buffer[]): void {
  for (const name of readdirSync(dir, { encoding: "buffer" })) {
    const path = Buffer.concat([dir, Buffer.from("/"), name]);
    const stat = lstatSync(path);
    if (stat.dev !== device) {
      continue;
    }
    if (stat.isDirectory()) {
      storesUnder(path, device, found);
    } else if (stat.isFile() && stat.size > 0 && isStore(path)) {
      found.push(path);
    }
  }
}

function storeIntact(path: Buffer): boolean {
  try {
    const db = openStore(path);
    try {
      return intact(db);
    } finally {
      db.close();
    }
  } catch {
    return false;
  }
}

/** Each store of the copy through `integrity_check`: the shared layer's and each image's. */
export function checkStores(copy: Copy): StoresCheck {
  const roots: string[] = [layout(copy.work).shared];
  let unread = 0;
  for (const pin of copy.manifest.snapshots) {
    const volume = copy.volume(pin.uid);
    if ("root" in volume) {
      roots.push(volume.root);
    } else {
      unread += 1;
    }
  }
  const found: Buffer[] = [];
  for (const root of roots) {
    storesUnder(Buffer.from(root), lstatSync(root).dev, found);
  }
  const whole = found.filter(storeIntact).length;
  return { stores: found.length, intact: whole, not_intact: found.length - whole,
           images_unread: unread };
}

/**
 * What the copy's output must never name: its path, and each owner's principal and key. A
 * principal is read from the shared layer's identities. Before the shared layer is extracted
 * (a copy refused at its digests) the path is all there is; once it is there, an identities
 * store that cannot be read throws, so the release lets nothing out (Fact 8).
 */
export function forbidden(work: string): string[] {
  const named = [work];
  try {
    named.push(realpathSync(work));
  } catch {
    // No copy yet: its path alone.
  }
  const identities = join(layout(work).shared, ".broker", "state", "owner-identities.db");
  let principals: string[] = [];
  if (existsSync(layout(work).shared)) {
    const db = openStore(identities);
    try {
      principals = db.prepare("SELECT principal FROM owner_identities").all()
        .map((row) => String(row["principal"]));
    } finally {
      db.close();
    }
  }
  for (const principal of principals) {
    named.push(principal, sha256Hex(principal).slice(0, 16));
  }
  return [...new Set(named.filter((text) => text !== ""))];
}

/** `text`, if it names nothing `forbidden` holds; else null, withheld. */
export function released(text: string, names: readonly string[]): string | null {
  return names.some((name) => text.includes(name)) ? null : text;
}

/** `copy <step> <work> ...`: its exit code (0 holds, 3 does not, 4 output withheld). */
export function copyMain(argv: readonly string[], copyOf: (work: string) => Copy,
                         print: (line: string) => void): number {
  const [, step, work] = argv;
  if (step === undefined || work === undefined) {
    throw new Error("usage: copy <plan|digests|stores|release> <work> ...");
  }
  if (step === "plan") {
    const at = argv.indexOf("--name");
    copyPlan(namedManifest(work, at < 0 ? "" : argv[at + 1] ?? "")).forEach(print);
    return 0;
  }
  if (step === "digests") {
    print(checkDigests(work, manifestOf(readFileSync(layout(work).manifest, "utf8"))));
    return 0;
  }
  if (step === "stores") {
    const found = checkStores(copyOf(work));
    print(JSON.stringify(found, null, 2));
    return found.not_intact === 0 ? 0 : 3;
  }
  if (step === "release") {
    const names = forbidden(work);
    let withheld = 0;
    for (const file of argv.slice(3)) {
      const text = released(readFileSync(file, "utf8"), names);
      if (text === null) {
        withheld += 1;
      } else if (text !== "") {
        print(text.replace(/\n$/, ""));
      }
    }
    if (withheld > 0) {
      print(`${withheld} of the job's outputs withheld: they name an owner or a path of the ` +
            "copy (kept 0600 in the run's work folder on the host)");
      return 4;
    }
    return 0;
  }
  throw new Error(`no copy step ${step}`);
}
