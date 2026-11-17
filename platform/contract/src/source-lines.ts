/**
 * How the platform's generators write TypeScript (`npm run generate`): the widest line its
 * sources keep, and a doc comment wrapped to it. The contract's, as the one layer every
 * generator and the ci kit may import (`ci/kit/layers.ts`).
 */

/** The widest line the platform's sources keep. */
export const WIDTH = 100;

/** `text` as a doc comment at `indent`, its words wrapped at the width; `what` names it. */
export function docLines(what: string, text: string, indent: string): string[] {
  if (text.includes("*/")) {
    throw new Error(`${what}: a description that ends a comment`);
  }
  const lines = [`${indent} *`];
  for (const word of text.split(/\s+/).filter((each) => each !== "")) {
    if (lines.at(-1)!.length + 1 + word.length > WIDTH && lines.at(-1) !== `${indent} *`) {
      lines.push(`${indent} *`);
    }
    lines[lines.length - 1] += ` ${word}`;
  }
  return [`${indent}/**`, ...lines, `${indent} */`];
}
