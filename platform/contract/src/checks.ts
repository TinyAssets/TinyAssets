/**
 * The platform's one home of shape checks, for the kits as for every Worker (the contract
 * runs under Node's type stripping, `guards/node.test.ts`, so nothing copies them):
 * - the tests (`object`, `keyed`, `among`, `whole`), for a reader to refuse in its own words,
 *   the value at a path through JSON objects (`valueAtPath`), and a data file frozen whole
 *   (`frozen`);
 * - the checks a source's sealed values are held to (`egress-scope.ts`, `model-record.ts`, the
 *   gateway's `credential.ts`): each refuses anything else as `invalid_source`, the gateway's
 *   fixed code, so nothing about the value crosses the boundary.
 */
import { Refusal } from "./refusal.ts";

/** Whether `value` is a JSON object: not an array, not null, not a class instance. */
export function object(value: unknown): value is Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return false;
  }
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

/**
 * Whether `value` is a JSON object of every one of `required` and any of `optional`, and no
 * other key: exactly `required` when nothing is optional. Order is not held.
 */
export function keyed(value: unknown, required: readonly string[],
                      optional: readonly string[] = []): value is Record<string, unknown> {
  if (!object(value)) {
    return false;
  }
  const keys = Object.keys(value);
  return required.every((key) => keys.includes(key))
    && keys.every((key) => required.includes(key) || optional.includes(key));
}

/** `value` and everything in it frozen, so no module can change a data file's for the isolate. */
export function frozen<T>(value: T): T {
  if (typeof value === "object" && value !== null && !Object.isFrozen(value)) {
    for (const each of Object.values(value)) {
      frozen(each);
    }
    Object.freeze(value);
  }
  return value;
}

/** Whether `value` is one of `list`. */
export function among<T>(list: readonly T[], value: unknown): value is T {
  return (list as readonly unknown[]).includes(value);
}

/** Whether `value` is a whole number from `min` to `max`. */
export function whole(value: unknown, min: number, max = Number.MAX_SAFE_INTEGER):
    value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= min
    && value <= max;
}

/** Whole Unix milliseconds representable by the file reader's ECMAScript Date. */
export function fileMtime(value: unknown): value is number {
  const dateLimitMs = 8_640_000_000_000_000;
  return whole(value, -dateLimitMs, dateLimitMs);
}

/** The value at `path` in `body`, or undefined. */
export function valueAtPath(body: unknown, path: readonly string[]): unknown {
  return path.reduce<unknown>((at, key) => (object(at) ? at[key] : undefined), body);
}

/** `value` as `keyed` holds it, or `invalid_source`. */
export function shape(value: unknown, required: readonly string[],
                      optional: readonly string[] = []): Record<string, unknown> {
  if (!keyed(value, required, optional)) {
    throw new Refusal("invalid_source");
  }
  return value;
}

export function text(value: unknown, pattern: RegExp, max = 1024): string {
  if (typeof value !== "string" || value.length > max || !pattern.test(value)) {
    throw new Refusal("invalid_source");
  }
  return value;
}

/** `value` as `whole` holds it, or `invalid_source`. */
export function count(value: unknown, min: number, max = Number.MAX_SAFE_INTEGER): number {
  if (!whole(value, min, max)) {
    throw new Refusal("invalid_source");
  }
  return value;
}

export function list<T>(value: unknown, min: number, max: number, each: (item: unknown) => T): T[] {
  if (!Array.isArray(value) || value.length < min || value.length > max) {
    throw new Refusal("invalid_source");
  }
  return value.map(each);
}
