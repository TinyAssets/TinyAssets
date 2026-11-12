/**
 * The checks a source's sealed values are held to (`egress-scope.ts`, `model-record.ts`, the
 * gateway's `credential.ts`): each refuses anything else as `invalid_source`, the gateway's
 * fixed code, so nothing about the value crosses the boundary.
 */
import { Refusal } from "./refusal";

export function object(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Whether `value` is an object of exactly `keys`, sorted and comma-joined. */
export function keyed(value: unknown, keys: string): value is Record<string, unknown> {
  return object(value) && Object.keys(value).sort().join() === keys;
}

/** Whether `value` is a whole number from `min` to `max`. */
export function whole(value: unknown, min: number, max = Number.MAX_SAFE_INTEGER):
    value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= min
    && value <= max;
}

/** Refuses `value` unless its keys are all in `allowed` and include every one of `required`. */
export function shape(value: unknown, required: readonly string[],
                      optional: readonly string[] = []): Record<string, unknown> {
  if (!object(value)) {
    throw new Refusal("invalid_source");
  }
  const keys = Object.keys(value);
  if (required.some((key) => !keys.includes(key))
      || keys.some((key) => !required.includes(key) && !optional.includes(key))) {
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
