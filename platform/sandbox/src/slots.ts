/**
 * The waiting line for one more running sandbox (`SandboxSlots`, a Durable Object of the
 * sandbox Worker's own, one by name for the whole platform): E2B runs as many sandboxes as
 * its plan allows, and an owner whose start found no room waits here, in the order it came,
 * never refused and never polled.
 *
 * - **A turn.** A start that needs one more running sandbox (a create, or a resume that is
 *   not a renewal's) may try only while no one waits, or when its owner is the first in
 *   line, so a newcomer never takes a freed slot ahead of an owner that waited longer.
 * - **The doorbell.** Each sandbox the platform pauses or kills (a release, an idle reap, an
 *   erasure, a renewal that found no run) rings the first owner in line: a message on the
 *   ring queue (`RING`), which the doors Worker takes to that owner's object
 *   (`sandboxRung`), whose waiting runs then start again through their whole admission. A
 *   start that got its sandbox leaves the line and rings the next (room may be left); one
 *   that found none again keeps its place; an owner with no run waiting leaves.
 * - **A renewal keeps its slot.** A renewing sandbox is paused for about a second, which
 *   frees a slot in E2B's count; it holds that slot here (`reserve`) across its pause and
 *   resume, and every start counts the slots held as taken (`turn`), so the first owner in
 *   line cannot create into it (the founder's rule: no run dies at the ceiling). Giving it
 *   back rings the line.
 * - **What no doorbell sees.** E2B's count is the team's: a sandbox outside the platform
 *   that ends, or E2B's own capacity coming back (`503`), rings nothing. So while anyone
 *   waits, the first in line is rung again, its wait doubling from `LEASE_TTL_S` (the
 *   transient failure's back-off, with no count) up to the plan's runtime ceiling, the
 *   longest any running sandbox runs before it is paused or renewed: a technical ceiling,
 *   not a poll of E2B.
 *
 * It holds owner ids and an order, nothing else; an owner's erasure takes its row.
 */
import { DurableObject } from "cloudflare:workers";

import { LEASE_TTL_S } from "../../contract/src/limits";
import { migrate } from "../../contract/src/migrate";

import { SLOTS_MIGRATIONS } from "./schema";
import type { SandboxEnv } from "./env";

/** The one line's name. */
export const SLOTS = "slots";

export class SandboxSlots extends DurableObject<SandboxEnv> {
  readonly #sql: SqlStorage;

  constructor(ctx: DurableObjectState, env: SandboxEnv) {
    super(ctx, env);
    this.#sql = ctx.storage.sql;
    migrate(ctx.storage, SLOTS_MIGRATIONS);
  }

  #head(): { owner: string; misses: number } | null {
    return this.#sql.exec<{ owner: string; misses: number }>(
      "SELECT owner, misses FROM waiting ORDER BY seq LIMIT 1").toArray()[0] ?? null;
  }

  /**
   * Whether `owner` may try for one more running sandbox now, and how many slots renewals
   * hold meanwhile (taken, whatever E2B's count says).
   */
  async turn(owner: string): Promise<{ turn: boolean; reserved: number }> {
    const head = this.#head();
    return { turn: head === null || head.owner === owner,
             reserved: this.#sql.exec<{ n: number }>("SELECT COUNT(*) AS n FROM held").one().n };
  }

  /** A renewing sandbox's slot held across its pause and resume. */
  async reserve(sandboxId: string): Promise<void> {
    this.#sql.exec("INSERT INTO held (sandbox_id) VALUES (?) ON CONFLICT DO NOTHING", sandboxId);
  }

  /** The slot given back (the sandbox runs again, or is gone): the line is rung. */
  async unreserve(sandboxId: string): Promise<void> {
    if (this.#sql.exec("DELETE FROM held WHERE sandbox_id = ? RETURNING sandbox_id", sandboxId)
      .toArray().length > 0) {
      await this.#ring();
    }
  }

  /**
   * `owner` found no room: it waits, in the place it first took. `ceilingS` is the plan's
   * runtime ceiling as the owner's start read it: the longest the first in line goes
   * unrung.
   */
  async wait(owner: string, ceilingS: number): Promise<void> {
    const line = this.#sql.exec<{ next_seq: number }>("SELECT next_seq FROM line").toArray()[0];
    const seq = line?.next_seq ?? 1;
    this.#sql.exec("INSERT INTO line (only, next_seq, ceiling_s) VALUES (1, ?, ?) ON CONFLICT " +
                     "(only) DO UPDATE SET next_seq = excluded.next_seq, ceiling_s = " +
                     "excluded.ceiling_s", seq + 1, ceilingS);
    this.#sql.exec("INSERT INTO waiting (owner, seq) VALUES (?, ?) ON CONFLICT (owner) DO " +
                     "NOTHING", owner, seq);
    await this.#arm();
  }

  /** `owner`'s start got its sandbox: it leaves the line, and the next is rung. */
  async took(owner: string): Promise<void> {
    if (this.#sql.exec("DELETE FROM waiting WHERE owner = ? RETURNING owner", owner)
      .toArray().length > 0) {
      await this.#ring();
    }
  }

  /** `owner` waits no more (no run of its waits, or it is erased); the next is rung. */
  async leave(owner: string): Promise<void> {
    const wasHead = this.#head()?.owner === owner;
    this.#sql.exec("DELETE FROM waiting WHERE owner = ?", owner);
    if (wasHead) {
      await this.#ring();
    }
  }

  /** A sandbox of the platform's paused or was killed: the first in line is rung. */
  async freed(): Promise<void> {
    await this.#ring();
  }

  async #ring(): Promise<void> {
    const head = this.#head();
    if (head !== null) {
      await this.env.RING.send({ owner: head.owner });
    }
    await this.#arm();
  }

  /** The first in line rung again after its back-off; no one waiting, no alarm. */
  async #arm(): Promise<void> {
    const head = this.#head();
    if (head === null) {
      await this.ctx.storage.deleteAlarm();
      return;
    }
    const ceilingMs = (this.#sql.exec<{ ceiling_s: number }>("SELECT ceiling_s FROM line")
      .toArray()[0]?.ceiling_s ?? LEASE_TTL_S) * 1000;
    const wait = Math.min(LEASE_TTL_S * 1000 * 2 ** head.misses, ceilingMs);
    await this.ctx.storage.setAlarm(Date.now() + wait);
  }

  /** The first in line's back-off ran out with no free rung: it is rung again, later next. */
  override async alarm(): Promise<void> {
    this.#sql.exec("UPDATE waiting SET misses = misses + 1 WHERE owner = (SELECT owner FROM " +
                     "waiting ORDER BY seq LIMIT 1)");
    await this.#ring();
  }
}
