#!/usr/bin/env bash
# Post a Drain-Review APPROVE receipt on a PR for its current head and cite it at the top of the PR body.
#   receipt.sh <pr> <evidence text file>
set -euo pipefail
PR="$1"; EVIDENCE="$2"
HEAD=$(gh pr view "$PR" --json headRefOid --jq .headRefOid)
TMP=$(mktemp -d)
{ printf 'Drain-Review-Verdict: APPROVE\nDrain-Review-Head: %s\n\n' "$HEAD"; cat "$EVIDENCE"; } > "$TMP/comment.md"
URL=$(gh pr comment "$PR" --body-file "$TMP/comment.md")
gh pr view "$PR" --json body --jq .body | grep -vE '^Drain-Review-(Verdict|Head|Artifact):' > "$TMP/old.md" || true
{ printf 'Drain-Review-Verdict: APPROVE\nDrain-Review-Head: %s\nDrain-Review-Artifact: %s\n\n' "$HEAD" "$URL"; cat "$TMP/old.md"; } > "$TMP/body.md"
gh pr edit "$PR" --body-file "$TMP/body.md" > /dev/null
echo "receipt for #$PR head $HEAD -> $URL"
