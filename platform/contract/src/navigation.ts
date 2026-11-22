/**
 * A browser's top-level navigation, as its Fetch Metadata request headers (`Sec-Fetch-*`)
 * report it. The doors serve their browser-only pages to one alone (`doors/src/authorize.ts`
 * `GET /authorize`, `doors/src/connect.ts` the connect callback), and the platform's canary
 * starts the sign-in as one (`deploy/canary.ts`), so the two read the one definition.
 */

/** The headers a browser's top-level navigation sends. */
export const NAVIGATE: Readonly<Record<string, string>> = Object.freeze({
  "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document" });

/** Whether `request` is a browser's top-level navigation. */
export function navigation(request: Request): boolean {
  return Object.entries(NAVIGATE).every(([name, value]) => request.headers.get(name) === value);
}
