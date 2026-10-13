// Plain JS on purpose: the node test suite drives this exact function.

/**
 * Stack-frame lines only. The first line of a V8 stack is "Name: message",
 * and an error message is free text that can carry anything a page held, so
 * the beacon never sends it. Frames are file, line and column.
 *
 * @param {unknown} stack
 * @param {number} [limit]
 * @returns {string}
 */
export function topFrames(stack, limit = 10) {
  if (typeof stack !== "string") return "";
  return stack
    .split("\n")
    .filter((line) => /^\s*at\s\S|^\S+@\S+:\d+/.test(line))
    .slice(0, limit)
    .map((line) => line.trim().slice(0, 300))
    .join("\n");
}
