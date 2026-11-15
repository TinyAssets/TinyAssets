/**
 * Byte arrays joined into one, the one join both the owner's files (`files.ts`, a range's
 * chunk reads) and its segments (`segments.ts`) read: a home of its own, so neither of those
 * modules imports the other for it.
 */
export function joined(parts: readonly Uint8Array[]): Uint8Array {
  const whole = new Uint8Array(parts.reduce((sum, part) => sum + part.byteLength, 0));
  let at = 0;
  for (const part of parts) {
    whole.set(part, at);
    at += part.byteLength;
  }
  return whole;
}
