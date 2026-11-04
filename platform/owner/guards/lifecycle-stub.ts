/**
 * The Agents SDK's Lifecycle, stubbed for the Node guards that import the owner's schema
 * (`migrations.test.ts`): the schema's imports reach `schedule.ts`, which extends
 * `LifecycleCapability`, and the real module loads `cloudflare:` runtime modules Node cannot.
 * Nothing here runs: the guards only read `MIGRATIONS`.
 */
export class LifecycleCapability {
  constructor(readonly id: string) {}
}

export class Lifecycle {}
