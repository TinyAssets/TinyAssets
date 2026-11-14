/**
 * The platform's one canonical JSON: what a value's digest is taken over (the policy
 * document's `_digest`, the sandbox image's tag) and how a value is stored to be compared
 * (a grant's scopes, a wake's detail). Python's `json.dumps(value, sort_keys=True,
 * separators=(",", ":"), ensure_ascii=False)`, for the JSON values a request carries.
 */
import { object } from "./checks.ts";

/** `value` as canonical JSON: every object's keys sorted, an undefined field left out. */
export function canonical(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map((item) => (item === undefined ? "null" : canonical(item))).join(",")}]`;
  }
  if (object(value)) {
    return `{${Object.keys(value).filter((key) => value[key] !== undefined).sort()
      .map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value) ?? "null";
}
