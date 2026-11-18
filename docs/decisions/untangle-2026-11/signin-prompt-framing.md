# Vendor prompt boundaries as observed (2026-11-18)

The sign-in runner and its deployment probe both append output as each pipe
chunk arrives, match the record's URL/code expressions, and keep their first
matches (`sandbox/runner/login.ts`, `runner.ts`, `probes.ts`). A pipe read
can end inside either value. The independent input-paste fix concerns a
different direction and has an explicit end to its private message.

## Boundaries in the pinned emitters

| Record | What terminates the displayed field |
| --- | --- |
| Codex 0.166.0, `login --device-auth` | The formatter follows the verification URL and the device code with an ANSI reset and line feed. It prints the whole prompt on stdout before polling. See [the pinned release source](https://github.com/openai/codex/blob/6ff1e1ca3b30fc8b914720681fcf5fe62e95f856/codex-rs/login/src/device_code_auth.rs#L149-L163). |
| Gemini 0.66.0, no browser on an interactive terminal | The user-code path writes the URL and two line feeds to stdout before reading the user's answer. The three shipped Gemini bundle variants that contain this message have identical terminators. See [the pinned release source](https://github.com/google-gemini/gemini-cli/blob/970413207732f5d4d8c50540c7fc359c44fdaaf2/packages/core/src/code_assist/oauth2.ts#L441-L468). |
| Kimi 2.4.1, global and mainland | In the pinned npm archive, `package/dist/main.mjs`'s CLI `onDeviceCode` writes the URL line and code line together to stderr using a line-feed join. Both regions use that callback. The complete verification URI takes precedence over the bare URI. See the [locked package](../../../platform/sources/locks/kimi/package-lock.json). |
| Claude Code 2.1.309, `auth login` | The pinned npm wrapper hands off to a platform executable. Its seven archive entries and the existing sign-in notes do not establish which bytes end the displayed URL while waiting for input. This boundary remains open. |

The Gemini, Kimi and Claude wrapper tarballs were checked against both the
version and SHA-512 in the lock and the source record. Codex was read from
the commit to which its annotated release tag points; this comparison did not
run its binary or attest its build. All values used for replay were synthetic.

## Offline replay on main f934871ad

The real record matchers, prompt capture, terminal cleanup and owner-side
shape check accepted the complete output in each of the four proved
configurations. A local util-linux pseudo-terminal produced the expected
CRLF for each line. Each output was split once at every byte boundary.
The table distinguishes a terminal refusal from an accepted wrong value.

| Fixture | Possible splits | Refused on main | Wrong value on main |
| --- | ---: | ---: | ---: |
| Codex, including its ANSI formatting | 489 | 12 | 6 |
| Gemini | 546 | 424 | 26 |
| Kimi global | 215 | 30 | 12 |
| Kimi mainland | 216 | 30 | 12 |

A line-aligned experiment before the same matcher returned the exact complete
URL and code for all 1,466 splits and for all four byte-at-a-time streams on
both Node 22 and 24. This experiment only measured complete line-delimited
examples. It did not run a vendor process, accept a real code, build a
template or adopt a new runtime reader.

Before implementation, obtain a sanitized byte-level observation of the
pinned Claude CLI up to its input wait, or equally authoritative emitter
source. Then review bounded accumulation, separate pipe state, UTF-8 and
terminal formatting across chunk boundaries, malformed output and the
ordinary pair verdict. The runner and probe should observe the same
complete prompts under the same record without per-vendor parsing branches.
