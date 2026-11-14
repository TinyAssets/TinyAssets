/**
 * The doors' public identity, the facts more than one Worker serves by (`identity.json`, its
 * one copy, which the doors' config, the deploy kit and the plugins read too): the first-party
 * resource, where the owner's own app is, whose tokens the doors take for `/app/api/*` and
 * whose pages the owner's object cites a file by (`owner/src/owner-do.ts`, `fileUrl`); and how
 * a route of its `routes` takes a URL.
 */
import identity from "../identity.json" with { type: "json" };

// Read as text (a template), so this module holds strings alone, never the shared file
// object (`ci/kit/module-state.ts`).
/** The first-party resource: the owner's own app, and its tokens' audience. */
export const APP_RESOURCE = `https://${identity.host}${identity.first_party.resource}`;

/**
 * Whether the Worker route `pattern` takes `url`, as Cloudflare matches one (its routes page):
 * against the whole URL a request carries, its query included, and never its fragment, which
 * no request carries. A pattern ending in `*` takes every URL it begins, any other only
 * itself; Cloudflare refuses a `*` anywhere else. Both are written alike, without a scheme:
 * from the host (the deploy kit's patterns) or from the path (`identity.json`'s).
 */
export function routeTakes(pattern: string, url: string): boolean {
  const star = pattern.indexOf("*");
  if (star !== -1 && star !== pattern.length - 1) {
    throw new Error(`the route ${pattern} holds a * before its end, which Cloudflare refuses`);
  }
  const sent = url.split("#", 1)[0]!;
  return star === -1 ? sent === pattern : sent.startsWith(pattern.slice(0, -1));
}
