#!/bin/bash
# Usage: ./check-sha.sh <sha>
#
# Uses the compare API (not the commits API) to reliably check
# whether a commit is on a specific branch. The commits API returns
# false positives on forks because GitHub shares the object store.

SHA=$1

check_branch() {
  local REPO=$1 BRANCH=$2
  local STATUS
  STATUS=$(gh api "repos/${REPO}/compare/${BRANCH}...${SHA}" --jq '.status' 2>/dev/null)
  case "$STATUS" in
    behind|identical) echo "YES" ;;
    *)                echo "no" ;;
  esac
}

# Get commit message (via upstream -- works even if not synced yet)
MSG=$(gh api "repos/eval-hub/eval-hub/commits/${SHA}" \
  --jq '.commit.message | split("\n")[0]' 2>/dev/null)
if [ -z "$MSG" ]; then
  MSG=$(gh api "repos/opendatahub-io/eval-hub/commits/${SHA}" \
    --jq '.commit.message | split("\n")[0]' 2>/dev/null)
fi

echo "=== Checking commit $SHA ==="
echo "    $MSG"
echo ""

echo "UPSTREAM (eval-hub/eval-hub):"
echo "  main:        $(check_branch eval-hub/eval-hub main)"

echo ""
echo "MIDSTREAM (opendatahub-io/eval-hub):"
echo "  main:        $(check_branch opendatahub-io/eval-hub main)"
echo "  incubation:  $(check_branch opendatahub-io/eval-hub incubation)"
echo "  stable:      $(check_branch opendatahub-io/eval-hub stable)"

echo ""
echo "DOWNSTREAM (red-hat-data-services/eval-hub):"
echo "  main:        $(check_branch red-hat-data-services/eval-hub main)"
