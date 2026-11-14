/**
 * The owner object's namespaces the workerd tests bind, by binding name, each to its class in
 * the test Worker (`test/worker.ts`): `vitest.config.ts` binds them and `env.d.ts` types
 * them.
 */
export const NAMESPACES = Object.freeze({
  OWNER: "OwnerDO",
  SEEDED: "SeededOwnerDO",
});
