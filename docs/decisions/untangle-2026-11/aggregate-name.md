# aggregate-name as built (2026-11-18)

Main-red reads the Tests aggregate's conclusion for a particular run attempt. Queue handoff
sets that same job aside when a failed leg names the failing tests. GitHub reports the job
by its display name, with its workflow job ID as the default. A stale name made main-red
read no conclusion and made queue handoff count an aggregate with no tests of its own as
an unexplained failure. The protected contexts in the ruleset cannot tell these readers
which job aggregated the Tests run.

Both scripts import the job name from `scripts/tests_workflow.py`. The process suite reads
the `required-tests` job from `.github/workflows/tests.yml` with its existing YAML parser
and passes that name through the main-red jq selector and the queue failure classifier.
The process suite already runs jq for the review workflow. A rename to the published
name now needs one Python edit.

The readers run under plain Python from the trusted checkout in main-red, queue-handoff,
auto-enroll and the enqueue helper. Both partial clones populate the checked-out files.
Parsing YAML in the gate scripts would add an installation to those jobs. Importing the
old stack's Python test runner would tie this lookup to its retiring shard machinery.
The shared name has no imports. Main-red quotes it as a JSON string in its jq predicate, so
a renamed job with quotes or backslashes can still be selected.

The ruleset guard already checks that the required context has exactly one reporter.
The queue-attempt fixtures use the shared aggregate name. Example shard names and the
JUnit artifact name identify different things and keep their own names.
