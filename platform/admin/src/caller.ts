/**
 * The platform's admin path (`owner/guards/admission-caller.test.ts`): the reserved caller
 * the owner's object takes `admissionSet` from, and no other door. Only the binder sends it
 * (`doors/src/binder.ts`, `bindPlatform`): the sign-up's explicit admit, at the sign-in
 * callback, when the owner directory holds no record of the owner (slice L).
 */
export const ADMIN_CALLER = "platform";
