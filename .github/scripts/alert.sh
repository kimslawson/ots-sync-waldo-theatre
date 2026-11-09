#!/usr/bin/env bash
# Keeps one GitHub issue, "Calendar feed update failing", in sync with the
# workflow's health. GitHub emails people about the issue (ALERT_MENTION is
# @-mentioned so the notification goes out even if they aren't watching).
#
#   alert.sh fail <log file>   open the issue, or comment if the error changed
#   alert.sh ok                close the issue if it's open
#
# Needs GH_TOKEN (issues: write) and the standard GITHUB_* variables.
set -euo pipefail

TITLE="Calendar feed update failing"
REPO="$GITHUB_REPOSITORY"
RUN_URL="$GITHUB_SERVER_URL/$REPO/actions/runs/$GITHUB_RUN_ID"

num=$(gh issue list --repo "$REPO" --state open --limit 100 --json number,title \
      | jq -r --arg t "$TITLE" 'map(select(.title == $t)) | .[0].number // empty')

case "${1:-}" in
  fail)
    log="${2:-}"
    if [ -s "$log" ]; then
      # From the first "error:" line on (the script's own explanation), else the tail.
      details=$(sed -n '/^error:/,$p' "$log" | head -80)
      [ -n "$details" ] || details=$(tail -40 "$log")
    else
      details="(no log: the run failed before the test or build steps)"
    fi
    # Same error as already reported? Don't send another email.
    sig=$(printf '%s' "$details" | sed -E 's/[0-9]+ attempts|[0-9.]+ s\b//g' | sha256sum | cut -c1-12)
    mention=""
    [ -z "${ALERT_MENTION:-}" ] || mention="@${ALERT_MENTION} "
    body=$(printf '%sThe calendar feed update failed, so the published feed was **not** changed; it still serves the last good version.\n\nRun: %s\n\n```\n%s\n```\n\nThis issue closes itself after the next successful run. See README.md, "When an alert arrives".\n\n<!-- sig:%s -->\n' \
      "$mention" "$RUN_URL" "$details" "$sig")
    if [ -z "$num" ]; then
      gh issue create --repo "$REPO" --title "$TITLE" --body "$body"
    elif ! gh issue view "$num" --repo "$REPO" --json body,comments \
           --jq '[.body] + [.comments[].body] | join("\n")' | grep -q "sig:$sig"; then
      gh issue comment "$num" --repo "$REPO" --body "$body"
    else
      echo "Issue #$num already reports this error; not notifying again."
    fi
    ;;
  ok)
    if [ -n "$num" ]; then
      gh issue close "$num" --repo "$REPO" --comment "Recovered in $RUN_URL"
    fi
    ;;
  *)
    echo "usage: alert.sh fail <log> | alert.sh ok" >&2
    exit 2
    ;;
esac
