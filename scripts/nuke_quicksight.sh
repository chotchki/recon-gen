#!/usr/bin/env bash
# Mass-delete EVERY QuickSight content resource across every reachable
# region. Built 2026-05-17 as the "$250/month canary kill switch" —
# Generative BI indexing storage was still ticking $0.04/day even after
# disabling the toggles (USE1-QuickSuite-Index line item). Deleting
# every dashboard / analysis / dataset / datasource / theme drops the
# indexed surface area to zero, so the storage meter physically can't
# fire.
#
# Scope:
#   - Dashboards / Analyses / Datasets / Datasources / Themes — DELETED
#   - Topics — already 0 (per disable_quicksight_genai.sh audit); script
#     still re-checks + nukes any that reappeared.
#   - Users / Namespaces / VPC connections — NOT TOUCHED. Those are
#     identity / network plumbing, not content. Manage separately.
#   - Folders — NOT TOUCHED (rarely used; manual decision).
#
# Order matters in QuickSight (delete dependents first):
#   1. Dashboards   (depend on analyses + datasets)
#   2. Analyses     (depend on datasets)
#   3. Themes       (no deps)
#   4. Datasets     (depend on datasources)
#   5. Topics       (depend on datasets — already deleted by step 4)
#   6. Datasources  (no deps after datasets gone)
#
# Regions scanned (configurable via REGIONS env): every region where
# QuickSight is available + the user has ever deployed. us-west-1 is
# included even though QS isn't available there (the call returns a
# clean "endpoint not reachable" which the script reports + skips).
#
# Usage:
#   ./scripts/nuke_quicksight.sh                # DRY-RUN: list what dies, no API delete calls
#   ./scripts/nuke_quicksight.sh --execute      # ACTUAL DELETE — requires --yes too
#   ./scripts/nuke_quicksight.sh --execute --yes
#
# Override profile / account / regions via env:
#   AWS_PROFILE=default AWS_ACCOUNT=470656905821 \
#     REGIONS="us-east-1 us-east-2 us-west-2" \
#     ./scripts/nuke_quicksight.sh --execute --yes

set -euo pipefail

# CI vs local: when OIDC creds are in env (GHA configure-aws-credentials
# action exports AWS_ACCESS_KEY_ID + AWS_SESSION_TOKEN), DON'T set
# AWS_PROFILE — the aws cli would then try to read a non-existent
# credentials file and fail with an empty error message (the symptom
# that bit the 2026-05-17 workflow smoke test). Local: default to
# `default` profile.
if [[ -n "${AWS_ACCESS_KEY_ID:-}" && -n "${AWS_SESSION_TOKEN:-}" ]]; then
    # CI / OIDC: env creds present, no profile lookup
    unset AWS_PROFILE 2>/dev/null || true
else
    AWS_PROFILE="${AWS_PROFILE:-default}"
fi
AWS_ACCOUNT="${AWS_ACCOUNT:-470656905821}"
# us-west-1 + us-west-2 added per user instruction 2026-05-17 — they
# consolidated to us-east-1 but want a defensive sweep across every
# region QS has ever been reachable from. eu-* included for the same
# reason. Override via REGIONS env.
REGIONS_DEFAULT="us-east-1 us-east-2 us-west-1 us-west-2 eu-west-1 eu-central-1"
REGIONS="${REGIONS:-$REGIONS_DEFAULT}"

EXECUTE=0
YES=0
for arg in "$@"; do
    case "$arg" in
        --execute) EXECUTE=1 ;;
        --yes)     YES=1 ;;
        --help|-h)
            sed -n '1,/^set -euo/p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        *) echo "Unknown arg: $arg" >&2; exit 2 ;;
    esac
done

if [[ $EXECUTE -eq 1 && $YES -ne 1 ]]; then
    echo "ERROR: --execute requires --yes (destructive op, no undo)." >&2
    exit 2
fi

# Only export AWS_PROFILE when set (CI/OIDC path leaves it unset above).
[[ -n "${AWS_PROFILE:-}" ]] && export AWS_PROFILE

bold()   { printf '\033[1m%s\033[0m\n' "$*"; }
green()  { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
red()    { printf '\033[31m%s\033[0m\n' "$*"; }
muted()  { printf '\033[2m%s\033[0m\n' "$*"; }

# Paginate any aws quicksight list-* command. Echo IDs only.
# Args: $1 verb (e.g. list-dashboards), $2 region, $3 jq-key
list_ids() {
    local verb="$1" region="$2" key="$3"
    local next_token=""
    while :; do
        local args=(aws quicksight "$verb"
            --aws-account-id "$AWS_ACCOUNT"
            --region "$region"
            --max-results 100
            --output json)
        if [[ -n "$next_token" ]]; then
            args+=(--next-token "$next_token")
        fi
        local result
        if ! result=$("${args[@]}" 2>&1); then
            # us-west-1 isn't a QS region; clean-skip with a log line
            if echo "$result" | grep -q "Could not connect to the endpoint URL"; then
                muted "  [$region] $verb: no QS endpoint (region not QS-enabled)" >&2
                return 0
            fi
            red "  [$region] $verb FAILED: $(echo "$result" | head -1)" >&2
            return 0
        fi
        echo "$result" | python3 -c "
import json, sys
d = json.load(sys.stdin)
key = '$key'
for item in d.get(next((k for k,v in d.items() if isinstance(v, list)), ''), []):
    print(item[key])
"
        next_token=$(echo "$result" | python3 -c "import json,sys; print(json.load(sys.stdin).get('NextToken','') or '')")
        [[ -z "$next_token" ]] && break
    done
}

# Delete one resource. Args: verb-id-flag verb-name region id
delete_one() {
    local kind="$1" region="$2" id="$3"
    local cmd_verb="delete-${kind%s}"  # dashboards → delete-dashboard
    # Special-case: data-sets → delete-data-set; data-sources → delete-data-source
    case "$kind" in
        data-sets)    cmd_verb="delete-data-set" ;;
        data-sources) cmd_verb="delete-data-source" ;;
    esac
    local id_flag
    case "$kind" in
        dashboards)   id_flag="--dashboard-id" ;;
        analyses)     id_flag="--analysis-id" ;;
        themes)       id_flag="--theme-id" ;;
        data-sets)    id_flag="--data-set-id" ;;
        topics)       id_flag="--topic-id" ;;
        data-sources) id_flag="--data-source-id" ;;
        *) red "  unknown kind: $kind" >&2; return 1 ;;
    esac
    if [[ $EXECUTE -eq 1 ]]; then
        if aws quicksight "$cmd_verb" \
            --aws-account-id "$AWS_ACCOUNT" \
            --region "$region" \
            $id_flag "$id" \
            --output text >/dev/null 2>&1; then
            green "    ✓ $kind/$id"
        else
            red   "    ✗ $kind/$id (delete failed — likely starter theme / in-use / cascaded already)"
        fi
    else
        yellow "    [DRY-RUN] would delete $kind/$id"
    fi
}

# Per-region sweep. Args: region
sweep_region() {
    local region="$1"
    bold "[$region]"
    # Note: 'themes' must come AFTER analyses (themes attach to analyses).
    # 'topics' must come AFTER data-sets (topics reference datasets).
    for kind in dashboards analyses themes data-sets topics data-sources; do
        echo "  $kind:"
        local ids
        ids=$(list_ids "list-$kind" "$region" "$(
            case $kind in
                dashboards)   echo DashboardId ;;
                analyses)     echo AnalysisId ;;
                themes)       echo ThemeId ;;
                data-sets)    echo DataSetId ;;
                topics)       echo TopicId ;;
                data-sources) echo DataSourceId ;;
            esac
        )" || true)
        if [[ -z "$ids" ]]; then
            green "    (none)"
            continue
        fi
        local count
        count=$(echo "$ids" | wc -l | tr -d ' ')
        yellow "    found $count"
        while IFS= read -r id; do
            [[ -z "$id" ]] && continue
            # Skip AWS-managed starter themes (cannot delete) — they don't
            # cost anything and DeleteTheme rejects them.
            if [[ "$kind" == "themes" && "$id" =~ ^(MIDNIGHT|SEASIDE|CLASSIC|RAINIER|AQUASCAPE|NITRO)$ ]]; then
                muted "    [skip] aws-builtin theme: $id"
                continue
            fi
            delete_one "$kind" "$region" "$id"
        done <<< "$ids"
    done
    echo
}

bold "==================================================================="
bold "  QuickSight mass-nuke"
bold "  Account:  $AWS_ACCOUNT   Profile: $AWS_PROFILE"
bold "  Regions:  $REGIONS"
if [[ $EXECUTE -eq 1 ]]; then
    red  "  Mode:     EXECUTE (irreversible)"
else
    yellow "  Mode:     DRY-RUN (no API delete calls — re-run with --execute --yes)"
fi
bold "==================================================================="
echo

for region in $REGIONS; do
    sweep_region "$region"
done

bold "Done."
if [[ $EXECUTE -ne 1 ]]; then
    echo
    yellow "Re-run with --execute --yes to actually delete."
fi
