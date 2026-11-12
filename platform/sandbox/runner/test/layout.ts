/**
 * The template's layout as a test sandbox's root holds it (`harness.ts`, `Box`), as plain
 * data, made from the image's own spec (`template/image.ts`): its users and groups, the
 * directories it builds and their owners and modes, and where Node, the loader and E2B's
 * egress CA sit. So the image and the test root share one definition. The users' ids are the
 * tests' own block (each box's pid namespace keeps every suite's processes apart, so a fixed
 * block is enough); the image's are its build's. Its pool is four run users, where the
 * image's is `RUNS_CEILING`. Each pool user's own group's gid is not its uid, since the
 * image's `useradd -U` makes a group of the user's name and promises no number. What E2B's
 * base image brings is the tests' own here: root, its
 * default user (in `sudo`, as its provisioning boots it), the system directories, and its
 * egress CA's path, which the loader names.
 */
import { spec } from "../../template/image";

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

const IMAGE = spec();

export const TA_GID = 50_000;
export const SYNC_UID = 50_001;
export const POOL_UIDS: readonly number[] = Object.freeze([50_002, 50_003, 50_004, 50_005]);
export const POOL_GIDS: readonly number[] = Object.freeze([50_102, 50_103, 50_104, 50_105]);
const POOL = POOL_UIDS.map((uid, n) => ({ name: `${IMAGE.pool.prefix}${n}`, uid,
                                          gid: POOL_GIDS[n]! }));
const E2B_UID = 1000;

export const USERS: readonly LayoutUser[] = Object.freeze([
  { name: "root", uid: 0, gid: 0, home: "/root", shell: "/bin/bash" },
  { name: IMAGE.e2b_user, uid: E2B_UID, gid: E2B_UID, home: `/home/${IMAGE.e2b_user}`,
    shell: "/bin/bash" },
  { name: IMAGE.sync.user, uid: SYNC_UID, gid: SYNC_UID, home: IMAGE.no_home,
    shell: IMAGE.sync.shell },
  ...POOL.map(({ name, uid, gid }) => ({ name, uid, gid, home: IMAGE.no_home,
                                         shell: IMAGE.pool.shell })),
]);

export const GROUPS: readonly LayoutGroup[] = Object.freeze([
  { name: "root", gid: 0, members: [] },
  { name: "sudo", gid: 27, members: [IMAGE.e2b_user] },
  { name: IMAGE.e2b_user, gid: E2B_UID, members: [] },
  { name: IMAGE.group, gid: TA_GID, members: POOL.map(({ name }) => name) },
  { name: IMAGE.sync.user, gid: SYNC_UID, members: [] },
  ...POOL.map(({ name, gid }) => ({ name, gid, members: [] })),
]);

/** A directory the image's `mkdir -p` makes and no step changes: root's umask 022. */
const MADE = 0o755;

export const DIRS: readonly LayoutDir[] = Object.freeze([
  { path: "/var", mode: 0o755, owner: "root", group: "root" },
  { path: "/var/lib", mode: 0o755, owner: "root", group: "root" },
  { path: IMAGE.state.dir, mode: IMAGE.state.mode, owner: "root", group: "root" },
  { path: IMAGE.state.inbox, mode: MADE, owner: "root", group: "root" },
  { path: IMAGE.state.incoming, mode: IMAGE.state.incoming_mode, owner: "root", group: "root" },
  { path: IMAGE.sync.dir, mode: IMAGE.sync.mode, owner: IMAGE.sync.user,
    group: IMAGE.sync.user },
  { path: "/var/tmp", mode: 0o1777, owner: "root", group: "root" },
  { path: "/tmp", mode: 0o1777, owner: "root", group: "root" },
  { path: "/run", mode: 0o755, owner: "root", group: "root" },
  { path: "/opt", mode: 0o755, owner: "root", group: "root" },
  { path: IMAGE.opt, mode: MADE, owner: "root", group: "root" },
  { path: IMAGE.runners.dir, mode: IMAGE.runners.mode, owner: "root", group: "root" },
  { path: IMAGE.engines_dir, mode: MADE, owner: "root", group: "root" },
  { path: IMAGE.workspace.dir, mode: IMAGE.workspace.mode, owner: "root",
    group: IMAGE.group },
]);

/** Node, under the image's prefix, and E2B's egress CA, where the loader trusts it. */
export const NODE = `${IMAGE.node.prefix}/bin/node`;
export const EGRESS_CA = "/usr/local/share/ca-certificates/e2b-ca.crt";
