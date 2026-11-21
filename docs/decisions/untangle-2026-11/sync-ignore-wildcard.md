# sync-ignore-wildcard as built (2026-11-18)

The sandbox cleanup removed the final caller of the contract's single-pattern
`wildcard(pattern, text)` helper (`pass-sandbox.md` §2). A repository-wide
source search at `f934871ad` found only its declaration. The contract is a
private workspace package. Production callers use `ignoredBy` to apply the
ordered ignore list, including directory and negation rules.

Removed the helper. Its tokenizer, automaton and byte conversion still have
callers in the list matcher and its performance measurement. The existing
contract tests compare the list matcher directly with Git.
