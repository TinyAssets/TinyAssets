/**
 * The owner's answers as the doors' tests read them, past what the doors read: the test
 * support's and the end-to-end entry's (`e2e-entry.ts`), which loads no test runtime.
 */
import type { DoorAnswer } from "../../contract/src/owner";
import type { PolicyDocument } from "../../contract/src/policy-document";
import type { DoorName } from "../src/tools";

/**
 * What these tests read of the owner's answers past what the doors read (`DoorAnswer`), as the
 * owner's handlers answer them (`owner/src`, which no doors test may import): a test's own
 * reading of the owner, which no deploy pairs with the doors, so the door table declares none
 * of it (`docs/decisions/untangle-2026-11/door-answers.md`).
 */
interface TestAnswers {
  readonly grantList: {
    readonly clients: readonly {
      readonly client: string; readonly source: string; readonly mode: string;
      readonly scopes: unknown; readonly revision: number; readonly revoked_at: number | null;
      readonly vendor: string | null; readonly vendor_verified: number }[] };
  readonly noticeList: {
    readonly notices: readonly { readonly type: string; readonly detail: string }[] };
  readonly outsideAgents: { readonly owner_agents: readonly { readonly agent: string }[] };
  readonly registerProposals: {
    readonly cards: readonly {
      readonly card_id: string; readonly digest: string; readonly client: string;
      readonly family: string; readonly request: Readonly<Record<string, unknown>> }[] };
  readonly registerAgent: { readonly agent_id: string };
  readonly starterReceipts: { readonly version: number };
  readonly fileRead: {
    readonly entry: { readonly size: number; readonly mode: number; readonly writer: string;
                      readonly mtime_ms: number };
    readonly inline?: Uint8Array; readonly chunks?: readonly { readonly sha256: string }[] };
  readonly fileStat: { readonly entry: { readonly kind: string } | null };
  readonly collectionGet: {
    readonly row: { readonly body: string; readonly private: boolean;
                    readonly writer: string } | null };
  readonly policyRead: { readonly generation: number; readonly policy: PolicyDocument };
  readonly sandboxDays: {
    readonly days: readonly {
      readonly day: string; readonly seconds: number; readonly billed_seconds: number;
      readonly vcpu_seconds: number; readonly gb_seconds: number }[] };
  readonly egressReceipts: {
    readonly receipts: readonly {
      readonly reservation: string; readonly method: string; readonly path: string;
      readonly state: string; readonly code: string | null; readonly ended: string | null;
      readonly outcome: string | null; readonly status: number | null;
      readonly bytes: number | null; readonly charge: string | null;
      readonly rule: string | null; readonly est_cost: number | null;
      readonly cost: number | null; readonly tokens: number | null }[] };
}

/** A door's answer as these tests read it: `TestAnswers`'s, else what the doors read. */
export type TestAnswer<D extends DoorName> = D extends keyof TestAnswers ? TestAnswers[D]
  : DoorAnswer<D>;

/** `answer`, of the door `door`, as these tests read it (`TestAnswer`). */
export function asTested<D extends DoorName>(_door: D, answer: DoorAnswer<D>): TestAnswer<D> {
  return answer as unknown as TestAnswer<D>;
}
