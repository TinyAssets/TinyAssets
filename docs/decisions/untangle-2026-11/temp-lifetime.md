# Temporary directory lifetime review as built (2026-11-18)

#5396 changed the owner object's temporary directory lifetime to seven days
and recorded the choice in the limits audit. The active file history spec
and method comment still said one day. The audit described a week of
inactivity. The deadline is set when the directory is created.

With a controlled clock on Node 22 and 24, the actual file store and all
18 migrations on `f934871ad` and combined `151c2a83f` created a directory
at second 1,800,000,000. It became due at 1,800,604,800. A housekeeping
pass after one day kept its file. Overwriting and reading it on day six
preserved the deadline. A pass one second before it was due retained the
directory. A pass exactly at the deadline removed the directory and its
contents, preserving a second directory created on day six. Explicit
cleanup removed a third directory immediately. When expiry was the next
work due, the scheduler allowed its usual one hour of housekeeping slack.

The in-object pi environment that allocated these directories was deleted
in #5389. Current hosted CLIs receive a sandbox-local `TMPDIR`. Current
production readers of stored `file_temps` rows include housekeeping, backup
export and restore. Export and restore carry `expires_at`. Existing file
limits, accounting and the frozen migrations still cover the reserved root.
