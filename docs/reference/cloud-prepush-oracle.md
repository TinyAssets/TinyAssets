# Cloud pre-push oracle

Hosted-only Linux proof for a working candidate **before** its runtime code is
pushed. Use it when a lane cannot run `scripts/linux_oracle.py` (no local
Docker, WSL or services). It is a supplement to CI, not a replacement; CI stays
authoritative.

Workflow: `.github/workflows/cloud-prepush-oracle.yml` (manual dispatch,
`contents: read`, no secrets, no environment, no deploy).
Helper: `scripts/cloud_prepush_oracle.py`.

## Usage

```bash
# 1. base = a full 40-hex sha already on GitHub; the diff is vs that base
git diff <base_sha> -- tinyassets/ tests/ scripts/ > candidate.patch

# 2. validate locally and build the dispatch inputs (exit 2 = rejected)
python scripts/cloud_prepush_oracle.py prepare --base <base_sha> \
    --patch candidate.patch --out inputs.json -- tests/test_owner_next_wake.py

# 3. dispatch by hand; nothing in the helper dispatches for you
gh workflow run cloud-prepush-oracle.yml --json < inputs.json
```

Read the job's step summary. Cite the base sha and patch sha256 it prints.

## What the job does

1. Checks out the **workflow revision** into `tooling/` (credentials not
   persisted). The helper always runs from here with `--repo candidate`, so
   the base commit never needs to contain the helper; older bases work.
2. Runs `validate-base` from the trusted tooling tree: the `base_sha` input
   must be full lowercase 40-hex **before** any checkout sees it.
3. Checks out `base_sha` into `candidate/` (credentials not persisted) and
   installs Python 3.11 + dev deps there, before the patch touches the tree.
4. `materialize`: decodes the bounded base64 raw-or-gzip patch, inflates gzip
   within the byte cap, checks the uncompressed patch's SHA256, the
   touched paths (only `tinyassets/`, `tests/`, `scripts/`; never the helper),
   credential-shaped tokens, and the pytest node ids (relative, under
   `tests/`, no option-like, absolute or traversal tokens). Confirms HEAD is
   the base and the tree is clean.
5. `apply`: `git apply --check` then `git apply` (never `--unsafe-paths`).
6. `run`: the selected node ids only, temp root under `runner.temp`.
7. `report`: writes `result.json`, prints the summary, uploads JUnit.

Inputs reach shell only through `env:`; no `${{ inputs.* }}` appears in any
`run:` block, and every subprocess is an exact argv list.

## Reading the verdict

| Verdict | Meaning |
|---|---|
| `PROOF` | Every selected test passed on Linux at base+patch. Zero skips. |
| `NOT-PROOF` | Something failed, errored, skipped or did not run. A skip is not a pass. Read the JUnit artifact. |
| `REJECTED` | Exit 2 at `validate-base` or `materialize`: an input failed validation. |

## Limits

| Input | Cap |
|---|---|
| `patch_b64` | 60,000 chars (GitHub caps the whole dispatch payload at 65,535) |
| Uncompressed patch | 256 KiB, raw or one gzip member; no trailing bytes |
| `tests_json` | 4,000 chars, 64 node ids |
| `base_sha` | full 40-hex only; branches, tags and short shas are rejected |

`prepare` preserves raw base64 for small patches and automatically uses gzip
with zero modification time when needed. It verifies byte-exact reconstruction
before writing inputs. The SHA256 always covers the uncompressed patch, never
the compressed transport. If still over the cap, use a nearer already-pushed
base where possible; never omit required code or tests to make a proof fit.
Oversized, truncated, corrupt, trailing-data or multi-member gzip is rejected,
never retried as raw input.

## Trust boundary

Only an operator-reviewed source/test diff belongs in this workflow. The candidate
runs Python on a disposable hosted runner; path checks are not a sandbox against
hostile Python, and the candidate can write its own test results. A green report
is evidence for the reviewed candidate and selected tests, not a security
attestation or proof of all platform behavior. SHA256 is integrity, not
authentication; bounded decompression necessarily occurs before hash checking.
No production credentials,
production service dependencies, or personal-desktop execution are involved.

The workflow must first land on the default branch before dispatch. Removing
this manual-only workflow is the rollback; it does not change production.
