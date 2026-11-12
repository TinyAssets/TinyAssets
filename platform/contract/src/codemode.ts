/**
 * The codemode Worker's RPC contract (`tinyassets-codemode`,
 * `docs/decisions/untangle-2026-11-wave-1.md` A1): its default entrypoint, as the owner's object
 * binds it (`CODEMODE_VM`), which the codemode Worker implements (`codemode/src/index.ts`) and
 * code mode calls (`owner/src/codemode.ts`).
 *
 * The request and the exchange are pi-codemode's own (`CodemodeRemoteRequest`,
 * `CodemodeRemoteExchange`, from `@earendil-works/pi-codemode/portable`), which both sides
 * import from the package; the contract imports no package, so it names them as its two
 * parameters.
 */

/**
 * One code mode execution, named by the owner (`<owner>/<random id>`): the VM runs in that
 * sandbox object, every tool call goes back through `exchange`, and the call resolves once
 * the VM has stopped.
 */
export interface CodemodeWorker<Request, Exchange> {
  codemodeExecute(name: string, request: Request, exchange: Exchange): Promise<void>;
}
