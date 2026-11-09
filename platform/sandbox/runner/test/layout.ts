/**
 * The template's layout as a test sandbox's root holds it (`harness.ts`, `Box`), as plain
 * data: its users and groups, the directories it builds and their owners and modes, and where
 * Node, the loader and E2B's egress CA sit. One module, so the template's own spec can take
 * its place. The users' ids are the tests' own block (each box's pid namespace keeps every
 * suite's processes apart, so a fixed block is enough); the template's are its build's. Its
 * pool is four run users, where the template's is `RUNS_CEILING`.
 */
import { ENGINE_ROOT, WORKSPACE } from "../engine";
import { INBOX, INCOMING, RUNNERS, STATE_DIR } from "../protocol";
import { POOL_PREFIX } from "../users";

export interface LayoutUser {
  readonly name: string;
  readonly uid: number;
  readonly gid: number;
  readonly home: string;
  readonly shell: string;
}

export interface LayoutGroup {
  readonly name: string;
  readonly gid: number;
  readonly members: readonly string[];
}

/** A directory the template builds, in order (each one's parent first). */
export interface LayoutDir {
  readonly path: string;
  readonly mode: number;
  readonly owner: string;
  readonly group: string;
}

export const TA_GID = 50_000;
export const SYNC_UID = 50_001;
export const POOL_UIDS: readonly number[] = Object.freeze([50_002, 50_003, 50_004, 50_005]);
const POOL = POOL_UIDS.map((uid, n) => ({ name: `${POOL_PREFIX}${n}`, uid }));

export const USERS: readonly LayoutUser[] = Object.freeze([
  { name: "root", uid: 0, gid: 0, home: "/root", shell: "/bin/bash" },
  // E2B's default user, whom its provisioning puts back in `sudo` at every boot.
  { name: "user", uid: 1000, gid: 1000, home: "/home/user", shell: "/bin/bash" },
  { name: "ta-sync", uid: SYNC_UID, gid: SYNC_UID, home: "/nonexistent",
    shell: "/usr/sbin/nologin" },
  ...POOL.map(({ name, uid }) => ({ name, uid, gid: uid, home: "/nonexistent",
                                    shell: "/bin/bash" })),
]);

export const GROUPS: readonly LayoutGroup[] = Object.freeze([
  { name: "root", gid: 0, members: [] },
  { name: "sudo", gid: 27, members: ["user"] },
  { name: "user", gid: 1000, members: [] },
  { name: "ta", gid: TA_GID, members: POOL.map(({ name }) => name) },
  { name: "ta-sync", gid: SYNC_UID, members: [] },
  ...POOL.map(({ name, uid }) => ({ name, gid: uid, members: [] })),
]);

export const DIRS: readonly LayoutDir[] = Object.freeze([
  { path: "/var", mode: 0o755, owner: "root", group: "root" },
  { path: "/var/lib", mode: 0o755, owner: "root", group: "root" },
  { path: STATE_DIR, mode: 0o700, owner: "root", group: "root" },
  { path: INBOX, mode: 0o700, owner: "root", group: "root" },
  { path: INCOMING, mode: 0o700, owner: "root", group: "root" },
  { path: "/var/lib/ta-sync", mode: 0o700, owner: "ta-sync", group: "ta-sync" },
  { path: "/var/tmp", mode: 0o1777, owner: "root", group: "root" },
  { path: "/tmp", mode: 0o1777, owner: "root", group: "root" },
  { path: "/run", mode: 0o755, owner: "root", group: "root" },
  { path: "/opt", mode: 0o755, owner: "root", group: "root" },
  { path: "/opt/ta", mode: 0o755, owner: "root", group: "root" },
  { path: RUNNERS, mode: 0o755, owner: "root", group: "root" },
  { path: ENGINE_ROOT, mode: 0o755, owner: "root", group: "root" },
  { path: WORKSPACE, mode: 0o2775, owner: "root", group: "ta" },
]);

/** Node v22 and E2B's egress CA (the loader is the protocol's `LOADER`). */
export const NODE = "/usr/local/bin/node";
export const EGRESS_CA = "/usr/local/share/ca-certificates/e2b-ca.crt";
