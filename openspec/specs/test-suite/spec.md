# Test suite

## Requirements

### Requirement: Every test verifies a current piece of the shape we are building

Every test SHALL name, through `pytest.mark.verifies`, at least one design row of an active change or one requirement of a main spec. A test whose ids are all unknown, superseded or deleted SHALL be removed. CI SHALL refuse a new or changed test without a resolvable id, and SHALL refuse a spec change that supersedes or deletes a row while tests still name it.

#### Scenario: Retiring a design retires its tests

- **WHEN** a spec change marks a design row superseded
- **THEN** CI fails until the same pull request deletes or re-points every test that names that row

#### Scenario: A test outside the shape cannot be added

- **WHEN** a pull request adds a test with no `verifies` id, or with an id no spec defines
- **THEN** the hygiene gate fails

### Requirement: A test already checked another way is pruned automatically

A scheduled job SHALL open a pull request deleting tests that name no current id, and tests whose covered lines for their id are a subset of another kept test's. Structural guards and shape probes SHALL count as kept checks.

#### Scenario: A duplicate test is proposed for removal

- **WHEN** two tests name the same row and one's covered lines are a subset of the other's
- **THEN** the next prune pull request deletes the subset test with a `Test-Removal: consolidated` line
