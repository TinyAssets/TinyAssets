# @tiny/design-system

TinyAssets' **React** design system — the "ink editorial" language. This is the
AI-facing component library: the compiled `dist/` is what claude.ai/design's
`/design-sync` ingests, and what the product site imports.

## Why React (2026)

The project is AI-first across all providers. Every AI design/codegen tool
(claude.ai/design, v0, shadcn+MCP, AI Elements, agent defaults) targets React,
and the training-data flywheel widens that lead. Tokens stay framework-neutral
(DTCG) so the design language is portable; the *components* are React.
Rationale: `2026-06-24-ai-first-design-system-alignment.md (archived in git)`.

## Layout

```
tokens/tiny.tokens.json   DTCG 2025.10 tokens — generated from the canonical CSS
tokens/extract-dtcg.mjs   CSS :root → DTCG (deterministic, no drift)
src/styles/tokens.css     canonical token :root (exact var names)
src/styles/base.css       reset + vocabulary (.voice/.ev/.readout/...)
src/components/<Name>/     <Name>.tsx .css .stories.tsx .schema.json .prompt.md
DESIGN.md                 framework-agnostic brief (the rules layer)
```

## Build

```bash
npm install
npm run build          # tokens → dist/{index.js,styles.css,index.d.ts} → manifest.json
npm run storybook      # component explorer (Storybook 10, React+Vite)
```

`dist/` contract: `index.js` (ESM, React external), `styles.css`
(tokens+base+component CSS, one import), `index.d.ts` (prop types),
`manifest.json` (per-component JSON schema + usage + token group counts).

## Use

```tsx
import "@tiny/design-system/styles.css";
import { Button, StatusPill, RitualLabel } from "@tiny/design-system";
```

## AI artifacts

- `tokens/tiny.tokens.json` — machine-readable tokens (DTCG).
- `dist/manifest.json` — per-component prop schemas + usage rules.
- `*.prompt.md` / `*.schema.json` per component — the contract + rules.
- `DESIGN.md` — the framework-agnostic design brief.
