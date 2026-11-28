# pi-start as built (2026-11-28)

`fix/pi-start`. Direction 2 (engine differences are data). The founder-approved template
build on main 3143e07dd (`sandbox-template.yml mode=build`, run 45307631080, image
`tinyassets-sandbox:sb31f0bce0edffd2a`, pi 1.6.0) failed its pair checks: every claude-code,
codex and gemini login and route passed, and all nine pi route probes failed in about a
second, `"reason":"no model request reached the door"`, `turns: [{exit: 1}]`, no request,
no connect.

## Research (Fact 14), before the fix

- **Reproduced.** pi 1.6.0 installed from the engine lock (`sources/locks/pi`, `npm ci
  --omit=dev`) and run as the record launches it (`--provider=anthropic
  --model=claude-fable-5-2 --no-approve --mode json`, the prompt on stdin, `HOME`,
  `PI_CODING_AGENT_DIR`, `PI_SKIP_VERSION_CHECK`, a placeholder key, `models.json` with the
  door's base) prints `Error: Unknown options: --provider, --model` on stderr and exits 1.
  The same with `--provider anthropic --model claude-fable-5-2` starts its turn and calls the
  base URL.
- **pi's parser, from both tarballs (`npm pack`, `dist/cli/args.js`).** `--provider` and
  `--model` are taken only as `arg === "--model"` with the next argument its value. An
  argument `--x=y` goes to `unknownFlags`, which `main.js` refuses as unknown options unless
  an extension registered that flag. 1.4.0's parser is the same on this point; 1.5.x and
  1.6.0 changed nothing here (CHANGELOG, and a diff of `args.js`: help text and env names
  only).
- **So pi 1.6.0 is not the cause; #5852 is.** `engine.ts`'s `modelArgs` rewrote every
  `--opt {placeholder}` pair of a record's `model_argv` into `--opt=<value>`, so an id that
  starts with `-` is never read as a flag. Until #5852 a start with no model skipped
  `model_argv`, and the probes named none, so pi ran its own default and the joined form was
  never handed to pi. #5852 made every start carry a model: from then on every pi run, live
  or probed, exits 1 before its first request. #5852's large route-probe tests passed
  because their CLIs are fakes, which ignore the argv's form.
- **The other three take the joined form.** claude-code, codex and gemini passed the same
  pair checks with `--model=<id>`, so they keep it.

## The fix

- Make the record's `model_argv` what runs: the runner fills it and no longer rewrites it.
  - claude-code, codex and gemini write `--model={model}` in their records, the argv they
    ran with before, so a reported id that starts with `-` is still never a flag for them.
  - pi's record keeps `--provider {provider} --model {model}`, pi's parser's only form; a
    value there that reads as a flag (`-x`) is refused before the run starts, loudly, as a
    bare placeholder already was.
  - `engine.ts` had a hidden link: a record said `--model {model}` and something else ran.
    The rule now lives in one place, the record.
- Make a failed probe turn carry its stderr's tail (`probes.ts`, 600 characters, the end,
  where a CLI says why it stopped). The probe read it before and put it only in the turn's
  own failure, which the request verdict ("no model request reached the door") replaced, so
  the CI log could not show why pi stopped (Fact 8). The pair's `passed.json` records only
  passing pairs, whose turns carry none, so its entries are unchanged.

## Tests

- `runner.test.ts` "passes pi its provider and model as separate arguments ...": pi's argv
  is `--provider <p> --model <id>`, and `-weird-model` is refused. Mutation: the old joining
  `modelArgs` fails it.
- The large "fails a CLI that does not complete its turn ..." now also runs a pi that exits
  1 with pi's refusal on stderr: each of its route lines names no request and carries that
  stderr. Mutation: the result's turns without `stderr` fail it. The fake CLI takes a
  `stderr` line for it (`test/harness.ts`).
- Not covered by a local test: real pi on the real argv. Only the pair check runs the
  image's CLIs; the reproduction above ran pi 1.6.0 by hand on both forms.

## Image and pair

The records are not image inputs (`template/image.ts` `tag`: `image.py`, the SDK lock, the
spec, the loader and the engines' locks), so the image stays `sb31f0bce0edffd2a`; the runner
and probes bundles change, so the pair is new.

- [ ] live: the template's pair check (`sandbox-template.yml`) on the merged head passes all
      nine pi route probes, then the pin PR records it.
