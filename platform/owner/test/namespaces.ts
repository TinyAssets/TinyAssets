/**
 * The owner object's namespaces the workerd tests bind, by binding name, each to its class in
 * the test Worker (`test/worker.ts`): `vitest.config.ts` binds them, `env.d.ts` types them
 * and `admitted.ts` admits each owner a test names in any of them.
 */
export const NAMESPACES = Object.freeze({
  OWNER: "OwnerDO",
  AGENT: "FauxOwnerDO",
  GATED: "GatedOwnerDO",
  BARE: "UnboundOwnerDO",
  BOUNDED: "BoundedOwnerDO",
  ARCHIVING: "ArchivingOwnerDO",
  BIGARCHIVING: "BigArchivingOwnerDO",
  DIVERGING: "DivergingOwnerDO",
  PURGING: "PurgingOwnerDO",
  RUNG: "RungOwnerDO",
  SWITCHED: "SwitchedOwnerDO",
  CACHED: "CachedOwnerDO",
  SEEDED: "SeededOwnerDO",
});
