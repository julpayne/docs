#!/usr/bin/env bash

set -euo pipefail

UPSTREAM_REPO="eval-hub/eval-hub"
SYNC_COMMIT_JQ='[.[] | select(.parents | length > 1)
  | select(.commit.message | startswith("Merge pull request"))
  | select(.commit.message | contains("eval-hub/main"))
][0] | .parents[1].sha'

find_synced_upstream_sha() {
  local downstream_repo="$1"
  local branch="${2:-main}"

  gh api "repos/${downstream_repo}/commits?sha=${branch}&per_page=50" --jq "${SYNC_COMMIT_JQ}"
}

find_upstream_pr_number() {
  local upstream_sha="$1"

  gh api "repos/${UPSTREAM_REPO}/commits/${upstream_sha}/pulls" --jq '.[0].number'
}

get_upstream_pr_title() {
  local pr_number="$1"

  gh pr view "${pr_number}" --repo "${UPSTREAM_REPO}" --json title --template '{{.title}}'
}

report_latest_synced_upstream_pr() {
  local label="$1"
  local downstream_repo="$2"
  local branch="${3:-main}"
  local upstream_sha pr_number

  echo "${label}"
  upstream_sha="$(find_synced_upstream_sha "${downstream_repo}" "${branch}")"
  echo "${upstream_sha}"

  pr_number="$(find_upstream_pr_number "${upstream_sha}")"
  get_upstream_pr_title "${pr_number}"

  echo ""
}

main() {
  echo "DOWNSTREAM"

  echo ""

  report_latest_synced_upstream_pr \
    "Latest upstream commit synced to downstream" \
    "red-hat-data-services/eval-hub" \
    "main"

  echo ""

  echo "MIDSTREAM"

  echo ""

  report_latest_synced_upstream_pr \
    "Latest upstream commit synced to midstream (main)" \
    "opendatahub-io/eval-hub" \
    "main"

  echo ""

  report_latest_synced_upstream_pr \
    "Latest upstream commit synced to midstream (incubation)" \
    "opendatahub-io/eval-hub" \
    "incubation"

  echo ""

  report_latest_synced_upstream_pr \
    "Latest upstream commit synced to midstream (stable)" \
    "opendatahub-io/eval-hub" \
    "stable"
}

main "$@"
