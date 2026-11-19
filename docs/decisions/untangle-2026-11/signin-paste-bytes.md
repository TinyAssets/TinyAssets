# signin-paste-bytes as built (2026-11-18)

1. **The terminal sets the unit.** At `f934871ad` the owner and runner compared the trimmed JavaScript string length with `SIGNIN_PASTE_MAX`. A Linux terminal in canonical mode delivered three synthetic 4,095-byte lines whole (ASCII, CJK and emoji plus ASCII). With 4,096, 4,098 and 4,096 input bytes respectively it delivered only the first 4,095 plus the newline. Both the real owner door and the sandbox input RPC took overlong Unicode in workerd.
2. **The input has one shape.** Wave 1 A1 puts vendor sign-in shapes in `sources`. Its `pastedSigninCode` checks printability before trimming, rejects an empty result and applies the existing contract's `utf8Bytes` to the result. The owner reports `invalid` with the byte limit. The sandbox control checks the same value before putting anything in the inbox. The runner checks it at the CLI. The app's single-line control trims its displayed value on submit. The owner's deadline, source switch, prompt and authority checks and the runner's `code_sent` event retain their meanings.
3. **The VM carries the bytes whole.** The input command reads and removes the private inbox file, then writes its bytes after the existing verb and newline. Its caller needs only write success or failure. The former one-caller send helper distinguished two failures that both became the same exit. The listener retains at most the frame limit and decodes complete UTF-8 once. Every split within a three-byte and four-byte character had changed its value with the old per-chunk decoder. An overlong or malformed frame never reaches the consumer. The unchanged ASCII renewal verbs use the same reader.
4. **Adoption.** The command, exit codes, socket framing and state file are compatible across versions. A new owner or sandbox Worker stops overlong inputs before an older runner. A live older runner still has its old fragmented-input handling until the ordinary idle runner adoption. The privileged runner suite and production pair check belong to their existing gates.

## Keep the paste request in its shared contract

- [x] On e0067de9 the ordinary source-isolation suite failed one of seven
  checks. SandboxControl imported the vendor source module for paste validation.
  The ten-stage sandbox rehearsal found the same failure despite all 140
  sandbox Worker and guard tests passing. Both are existing tests.
- [x] Moved the existing validator beside the SandboxService.signinInput
  request. Owner, Sandbox Worker and VM runner use it there. Source-specific
  prompt and argument rules stay with their records. The unchanged isolation
  suite passed all seven cases. All 351 standalone and 355 combined source
  cases passed. The old and new validator returned identical results on
  1,114,210 inputs per Node 22 and 24, including every single Unicode code
  point and trimmed UTF-8 lines around the terminal byte boundary.
- [ ] Full platform types and end-to-end checks are being verified on the
  staged source and the local rehearsal. Coordinator integration, pinned
  sandbox acceptance and the separate workspace retry work remain open.
