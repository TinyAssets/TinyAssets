#!/usr/bin/env bash
# Native driver for deploy/native/NATIVE-TEST-PLAN.md.
#
# NOT RUN by this change, and not run by the correction that revised it.
# Authored for the coordinator to inspect and execute. It runs only the rows
# the current privilege context can actually reach; every other row prints
# SKIP or NOT_PROVEN with its reason and its name. Neither is a pass, and no
# row is recorded from an exit status that would be the same either way.
#
#   usage: bash deploy/native/ta_op_native_check.sh /path/to/ta-op
#
# No mode invokes a model provider (the platform has no LLM, Hard Rule 15),
# and this driver never touches production.
set -uo pipefail

BIN="${1:?path to a compiled ta-op required}"
[ -x "$BIN" ] || { echo "FATAL: $BIN is not executable"; exit 2; }

pass=0; fail=0; skip=0; unproven=0
ok()   { echo "PASS  $1"; pass=$((pass + 1)); }
bad()  { echo "FAIL  $1  -- $2"; fail=$((fail + 1)); }
skp()  { echo "SKIP  $1  -- $2"; skip=$((skip + 1)); }
np()   { echo "NOT_PROVEN  $1  -- $2"; unproven=$((unproven + 1)); }

# An identity refusal is a different row's result, never this row's.
IDENTITY_TAGS='unexpected-entry-uid|exact-bootstrap-caps|legacy-entry-|nnp-readback|uid-gid-readback|fs-uid-readback|fs-gid-readback|group-readback'

# Refuses with exit 78 and the expected TA_OP_REFUSED tag.
expect_refusal() {
  local name="$1" want="$2"; shift 2
  local out rc
  out="$("$BIN" "$@" 2>&1)"; rc=$?
  if [ "$rc" -ne 78 ]; then bad "$name" "exit $rc, wanted 78 ($out)"; return; fi
  case "$out" in
    *"TA_OP_REFUSED:${want}"*) ok "$name" ;;
    *) bad "$name" "wanted TA_OP_REFUSED:${want}, got: $out" ;;
  esac
}

# Same, for a row reachable only after the identity guard passes: an identity
# tag is NOT_PROVEN for this row rather than a failure of it.
expect_refusal_post_identity() {
  local name="$1" want="$2"; shift 2
  local out rc
  out="$("$BIN" "$@" 2>&1)"; rc=$?
  if echo "$out" | grep -Eq "$IDENTITY_TAGS"; then
    np "$name" "identity guard refused before this check ran: $out"; return
  fi
  if [ "$rc" -ne 78 ]; then bad "$name" "exit $rc, wanted 78 ($out)"; return; fi
  case "$out" in
    *"TA_OP_REFUSED:${want}"*) ok "$name" ;;
    *) bad "$name" "wanted TA_OP_REFUSED:${want}, got: $out" ;;
  esac
}

echo "=== rows that are genuinely uid-independent (mode and arity precede the guard) ==="
expect_refusal "1  unknown mode"            unknown-mode        shell
expect_refusal "1b unknown mode (alias)"    unknown-mode        sh
expect_refusal "2  arity: pulse + extra"    arity               pulse extra
expect_refusal "2b arity: bare printenv"    arity               printenv
out="$("$BIN" 2>&1)"; rc=$?
if [ "$rc" -eq 78 ] && [ "${out#*no-mode}" != "$out" ]; then
  ok "4  no mode at all"
else
  bad "4  no mode at all" "exit $rc: $out"
fi

echo "=== identity-dependent rows ==="
uid="$(id -u)"
guard_ok=0
guard_why="not probed"
case "$uid" in
  0)
    skp "6  rootless exact groups" "running as root; re-run as uid 1001"
    caps="$(grep -E '^CapEff' /proc/self/status | awk '{print $2}')"
    out="$("$BIN" version 2>&1)"; rc=$?
    echo "NOTE  entry uid=0 CapEff=$caps — row 10/11/12 discrimination is the container's cap_add set"
    if [ "$rc" -eq 0 ]; then
      case "$out" in
        "ta-op 1 modes="*) ok "10 root + bootstrap caps: full drop then version"; guard_ok=1 ;;
        *) bad "10 root + bootstrap caps" "unexpected banner: $out" ;;
      esac
    else
      guard_why="$out"
      case "$out" in
        *"TA_OP_REFUSED:exact-bootstrap-caps"*) ok "11/12 non-exact cap set refused" ;;
        *) bad "10 root + bootstrap caps" "exit $rc: $out" ;;
      esac
    fi
    ;;
  1001)
    out="$("$BIN" version 2>&1)"; rc=$?
    if [ "$rc" -eq 0 ]; then
      case "$out" in
        "ta-op 1 modes="*) ok "6  rootless exact groups: verified, version printed"; guard_ok=1 ;;
        *) bad "6  rootless exact groups" "unexpected banner: $out" ;;
      esac
    else
      guard_why="$out"
      # Rows 7/8/9 land here; report which discriminator fired.
      case "$out" in
        *legacy-entry-unexpected-group*) ok "7  foreign supplementary group refused" ;;
        *legacy-entry-caps-not-empty*)   ok "8  MUTATION CONTROL: cap-bearing entry refused" ;;
        *nnp-readback*)                  ok "9  missing no-new-privileges refused" ;;
        *) bad "6  rootless entry" "exit $rc: $out" ;;
      esac
    fi
    ;;
  *)
    expect_refusal "5  unexpected entry uid" unexpected-entry-uid version
    guard_why="uid $uid is neither 0 nor 1001"
    skp "6  rootless exact groups" "current uid is $uid, not 1001"
    skp "10 root + bootstrap caps" "current uid is $uid, not 0"
    ;;
esac

echo "=== row 3: malformed NAME — reachable only AFTER the identity guard passes ==="
# main() drops/verifies identity before it validates the NAME, so at any other
# posture this row would record an identity refusal as a NAME result.
if [ "$guard_ok" -eq 1 ]; then
  expect_refusal_post_identity "3  malformed NAME: spaces"  env-name  printenv "a b"
  expect_refusal_post_identity "3b malformed NAME: lower"   env-name  printenv lower
  expect_refusal_post_identity "3c malformed NAME: digit"   env-name  printenv 9X
  expect_refusal_post_identity "3d malformed NAME: empty"   env-name  printenv ""
else
  np "3  malformed NAME (4 cases)" "identity guard does not pass here: $guard_why"
fi

echo "=== row 14: descriptor boundary ==="
np "14 descriptor boundary" "an exit status cannot show it: printenv exits 0 whether or not fd 9 was closed. Run the strace check in NATIVE-TEST-PLAN.md (close(9) must precede execve)"

echo "=== row 16: env-summary filters on the NAME, never the value ==="
if [ "$guard_ok" -eq 1 ]; then
  out="$(FOO=1 TINYASSETS_GOAL_POOL=off SECRET_TOKEN=ollama-token "$BIN" env-summary 2>&1)"
  if echo "$out" | grep -Eq "$IDENTITY_TAGS"; then
    np "16 env-summary" "identity guard refused before the builtin ran: $out"
  elif [ "${out#*SECRET_TOKEN}" != "$out" ]; then
    bad "16 env-summary" "a value-only match leaked: $out"
  elif [ "${out#*TINYASSETS_GOAL_POOL=off}" != "$out" ]; then
    ok "16 env-summary: name match printed, value match withheld"
  else
    bad "16 env-summary" "expected flag missing: $out"
  fi
else
  np "16 env-summary" "post-drop builtin; identity guard does not pass here: $guard_why"
fi

echo "=== row 17: installed mode/ownership (only when the install path exists) ==="
if [ -e /usr/local/libexec/ta-op ]; then
  mode="$(stat -c %a /usr/local/libexec/ta-op)"; owner="$(stat -c %U /usr/local/libexec/ta-op)"
  if [ "$mode" = "555" ] && [ "$owner" = "root" ]; then
    ok "17 installed root-owned 0555"
  else
    bad "17 installed root-owned 0555" "mode=$mode owner=$owner"
  fi
else
  skp "17 installed root-owned 0555" "/usr/local/libexec/ta-op not present here"
fi

echo
echo "rows: pass=$pass fail=$fail skip=$skip not_proven=$unproven"
echo "rows 13, 15 and 18 are deliberately NOT automated here: 13 and 15 need strace"
echo "and a target that prints /proc/self/status, and 18 needs a second binary built"
echo "with -DTA_STATUS_PATH against a crafted file. Run them by hand per"
echo "NATIVE-TEST-PLAN.md. NOT_PROVEN rows have not been tested, in either direction."
[ "$fail" -eq 0 ] || exit 1
