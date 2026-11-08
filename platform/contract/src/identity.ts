/**
 * The doors' public identity, the facts more than one Worker serves by (`identity.json`, its
 * one copy, which the doors' config, the deploy kit and the plugins read too): the first-party
 * resource, where the owner's own app is, whose tokens the doors take for `/app/api/*` and
 * whose pages the owner's object cites a file by (`owner/src/owner-do.ts`, `fileUrl`).
 */
import identity from "../identity.json" with { type: "json" };

// Read as text (a template), so this module holds strings alone, never the shared file
// object (`owner/guards/module-state.test.ts`).
/** The first-party resource: the owner's own app, and its tokens' audience. */
export const APP_RESOURCE = `https://${identity.host}${identity.first_party.resource}`;
