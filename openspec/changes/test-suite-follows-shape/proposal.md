# The test suite follows the architecture we are building

## Why

On 2026-10-20 the required suite was 25,158 tests in 1,247 files, about two test-hours. Every C6 lane spent 1–1.5 hours running it locally, and half the time sat in about 50 files. Most of those tested paths the researched per-owner shape deletes, or repeated what another test or a structural guard already checks. Tests were only ever added. Nothing removed them when the design that justified them changed.

The founder's rules (2026-10-21):
- "if its not needed to verify the researched shape or already checked in another way, then that test is eligible to be cut";
- "this is how the test suite should work, auto pruning based on our best known architecture at the time towards the shape we are building and deploying".

## What Changes

- **Every test names what it verifies.** Each test module (or test) carries `pytest.mark.verifies("<id>", ...)`. The id is a design row of an active change (for example `owner-daemon-in-box:C11`) or a requirement of a main spec (`<capability>:<requirement title>`).
- **One script resolves ids against the specs.** `scripts/test_shape.py` reads the active changes' target-shape and correction rows and the main specs' requirements. It reports every test whose ids are unknown, superseded or deleted.
- **CI enforces it, in the existing hygiene gate:**
  - a new or changed test without a resolvable `verifies` fails;
  - a spec change that supersedes or deletes a row fails unless the same PR deletes or re-points that row's tests.

  Retiring a design retires its tests in the same change.
- **Duplicates are found from coverage.** CI's coverage run records each test's covered lines. A test whose covered lines for its row are a subset of another kept test's is a removal candidate.
- **A scheduled job opens one prune PR** listing orphaned and duplicate tests, with the deletions made and `Test-Removal` lines written. It merges after the ordinary single review.
- **Deleted:** the expectation that tests are permanent. A test's life is its row's life.

## Impact

- Touches every test module once, to add the marker. That's done after the C6 cutover, so it doesn't collide with the C6 lanes.
- The hygiene gate gains two checks and loses none.
- Structural guards and `check_shapes` probes stay; they are among the "other ways" a behaviour is checked.
