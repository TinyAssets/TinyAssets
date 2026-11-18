# b26-spec-references as built (2026-11-18)

The owner spec still described a 31 MiB V8 serialization limit called
`CODE_ANSWER_MAX`. The owner actually measures UTF-8 bytes of the JSON answer.
If the value fits, it preserves the value and fits as much output as possible.
When fitting still fails after dropping the output, the `answer_too_large`
refusal keeps the call counts and landed changes even if their summary alone
exceeds a page. The spec now describes both cases. The refusal
itself gives its budget in bytes, and the existing real door test checks the
unit in the message. The sandbox template's run-user pool reads the contract's
`control.ts`; the spec pointed to a deleted owner tier module.

The CI guard checks literal repository paths under `platform/` and workspace
file paths, inline uppercase names containing an underscore, and Markdown
links that cite an exported constant. It reads identifiers and exact string
values in workspace source, sandbox runner code and configuration. A local constant
link must take the reader to its defining module and, if given, the declaration's
line range. Test fixtures and memory probes cannot satisfy an unlinked name. File and binding names in runtime examples,
such as the `files/read` scope, do not imply checkout paths.

On main, the guard identifies both the missing tier module and the retired
answer limit. Changing the corrected page to cite a module that only imports
`PAGE_BYTES` fails, as do reintroducing the retired limit and removing the
cited schema path. The active specs currently cite 83 distinct uppercase
names of the form checked. Method names in prose, external APIs and example
payloads still need the B26 area reviews.

The full CI workspace on the reviewed tree passed 335 tests with one skipped.
The typecheck and all 11 owner code-door tests passed. On the final source,
the canonical recorders matched main byte for byte: 804 wire lines, seven tool
lines, 13,622 RPC lines and 73,945 RV lines. The oversized-answer wording is
exercised by the focused code-door test. An interrupted recorder and an
incomplete worker run supplied no lines to the final RV comparison.
