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
  - pi's record keeps `--provider {provider} --model {model}`, pi's parser's only form.
  - `engine.ts` had a hidden link: a record said `--model {model}` and something else ran.
    The form now lives in one place, the record.
- Refuse a value that would read as a flag on every argument, not on the model's alone
  (review of #5858). `argOf` fills each argument of the argv and refuses the run when a
  value made it start with `-` (its template did not). Before, only `modelArgs` refused it,
  and `resume_argv`'s bare `{sid}` went in unchecked. A session id comes from the untrusted
  VM's CLI line (`runner.ts`), so `--dangerously-skip-permissions` as an id would have
  resumed claude-code, codex and gemini with that flag. pi takes the next argument verbatim,
  so it alone was safe. `modelArgs` was then surplus and is gone, and so is its second copy
  of `fill`'s placeholder grammar.
- The owner refuses to keep such an id (`owner/src/runs.ts` `SESSION_ID`, no leading `-`),
  as it refuses one with a space: the run fails with `session_id_refused`, and the id is
  never handed back. The table's CHECK stays wider. Narrowing it needs a migration, and the
  object's own gate is the only writer.
- Make a failed probe turn carry its stderr's tail (`probes.ts`, 600 characters, the end,
  where a CLI says why it stopped). The probe read it before and put it only in the turn's
  own failure, which the request verdict ("no model request reached the door") replaced, so
  the CI log could not show why pi stopped (Fact 8). The pair's `passed.json` records only
  passing pairs, whose turns carry none, so its entries are unchanged.

## Tests

- `runner.test.ts` "passes pi its provider and model as separate arguments ...": pi's argv
  is `--provider <p> --model <id>`, and `-weird-model` is refused. Mutation: the old joining
  `modelArgs` fails it.
- `runner.test.ts` "resumes %s by its session id, and refuses one that would read as a
  flag", once per engine record. Mutation: `resume_argv` filled by plain `fill` fails all
  four.
- `owner/test/runs.test.ts` "keeps the run ... a session id outside its shape is refused":
  `--dangerously-skip-permissions` is refused and not kept. Mutation: the old `SESSION_ID`
  fails it.
- The large "fails a CLI that does not complete its turn ..." now also runs a pi that exits
  1 with pi's refusal on stderr: each of its route lines (one per pi step of the plan,
  checked, so never none) names no request and carries that stderr. Mutation: the result's turns without `stderr` fail it. The fake CLI takes a
  `stderr` line for it (`test/harness.ts`).
- Not covered by a local test: real pi on the real argv. Only the pair check runs the
  image's CLIs; the reproduction above ran pi 1.6.0 by hand on both forms.

## Image and pair

The records are not image inputs (`template/image.ts` `tag`: `image.py`, the SDK lock, the
spec, the loader and the engines' locks), so the image stays `sb31f0bce0edffd2a`; the runner
and probes bundles change, so the pair is new.

- [ ] live: the template's pair check (`sandbox-template.yml`) on the merged head passes all
      nine pi route probes, then the pin PR records it.
- [ ] fix/route-model: the route verdict (`sandbox/runner/route.ts` `routeVerdict`) also
      checks that each model request names the run's model (its body's `model`, or the
      Generative Language path's), so the pair check proves the CLI runs the model it was
      given, not only that it reaches the door (review of #5858, pre-existing).
