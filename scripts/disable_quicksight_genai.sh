#!/usr/bin/env bash
# AWS QuickSight cost audit + Generative BI opt-out.
#
# Generative BI opt-out reference:
# https://docs.aws.amazon.com/quick/latest/userguide/generative-bi-opt-out.html
#
# Four Generative BI cost drivers (steps 1-4):
#   1. Pro users (ADMIN_PRO / AUTHOR_PRO / READER_PRO roles)
#   2. Topics (deleted across every region)
#   3. Dashboard + visual indexing (account-level toggle)
#   4. Dashboard Q&A (account-level toggle)
#
# Plus four additional cost surfaces (steps 5-8) that the GenBI opt-out
# doc doesn't cover but commonly drive QuickSight bills:
#   5. User licenses (Author/Admin seats — Enterprise edition is ~$24/mo each)
#   6. VPC connections (per-hour fee per connection)
#   7. SPICE capacity (provisioned even if unused)
#   8. Resource sprawl (dashboards/analyses/datasets — bigger surface = more indexed rows)
#
# Usage:
#   ./scripts/disable_quicksight_genai.sh audit       # read-only — all 8 surfaces
#   ./scripts/disable_quicksight_genai.sh disable     # destructive — disables 3+4 + lists residue elsewhere
#   ./scripts/disable_quicksight_genai.sh verify      # re-runs audit, expects everything clean
#
# Requires an IAM principal with quicksight:List* + Update* + DeleteTopic,
# plus rds:DescribeVPCConnections and ce:GetCostAndUsage. Root or full-admin
# is easiest. The `recon-gen-local` IAM user is intentionally narrow and will
# hit AccessDenied on most of these. Run with an admin profile:
#   AWS_PROFILE=default ./scripts/disable_quicksight_genai.sh audit

set -euo pipefail

AWS_PROFILE="${AWS_PROFILE:-recon-gen-local}"
AWS_ACCOUNT="${AWS_ACCOUNT:-470656905821}"
IDENTITY_REGION="${IDENTITY_REGION:-us-east-1}"
# Regions to scan for Topics. Topics are regional — scan everywhere you might
# have ever deployed. Add to this list if you ever expanded.
SCAN_REGIONS=(us-east-1 us-east-2 us-west-1 us-west-2 eu-west-1 eu-central-1)

export AWS_PROFILE

bold()   { printf '\033[1m%s\033[0m\n' "$*"; }
green()  { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
red()    { printf '\033[31m%s\033[0m\n' "$*"; }

audit_step1_pro_users() {
    bold "Step 1 — Pro users (identity region: $IDENTITY_REGION)"
    local pro_users
    pro_users=$(
        aws quicksight list-users \
            --aws-account-id "$AWS_ACCOUNT" \
            --namespace default \
            --region "$IDENTITY_REGION" \
            --query 'UserList[?contains(Role, `PRO`)].{Name:UserName,Role:Role}' \
            --output table 2>&1
    ) || { red "  list-users failed: $pro_users"; return 1; }
    if echo "$pro_users" | grep -q PRO; then
        red "  Pro users found:"
        echo "$pro_users"
        yellow "  Action: downgrade with update-user (set --role to ADMIN/AUTHOR/READER) or delete-user."
    else
        green "  OK — no Pro users."
    fi
}

audit_step2_topics() {
    bold "Step 2 — Topics (scanning ${#SCAN_REGIONS[@]} regions)"
    local found=0
    for r in "${SCAN_REGIONS[@]}"; do
        local topics
        topics=$(
            aws quicksight list-topics \
                --aws-account-id "$AWS_ACCOUNT" \
                --region "$r" \
                --query 'TopicsSummaries[].{Id:TopicId,Name:Name}' \
                --output text 2>&1
        ) || { yellow "  [$r] list-topics failed: $topics"; continue; }
        if [[ -n "$topics" && "$topics" != "None" ]]; then
            red "  [$r] Topics found:"
            echo "$topics" | sed 's/^/    /'
            found=1
        else
            green "  [$r] OK — no topics."
        fi
    done
    if [[ $found -eq 1 ]]; then
        yellow "  Action: delete each with: aws quicksight delete-topic --aws-account-id $AWS_ACCOUNT --region <r> --topic-id <id>"
    fi
}

audit_step3_indexing() {
    bold "Step 3 — Dashboard + visual indexing (account-level, $IDENTITY_REGION)"
    local cfg
    cfg=$(
        aws quicksight describe-quick-sight-q-search-configuration \
            --aws-account-id "$AWS_ACCOUNT" \
            --region "$IDENTITY_REGION" \
            --output json 2>&1
    ) || { red "  describe failed: $cfg"; return 1; }
    echo "$cfg" | sed 's/^/  /'
    if echo "$cfg" | grep -q '"QSearchStatus": "ENABLED"'; then
        yellow "  Action: ./scripts/disable_quicksight_genai.sh disable  (or run manually with --q-search-status DISABLED)"
    else
        green "  OK — Q search DISABLED."
    fi
}

audit_step4_dashboard_qa() {
    bold "Step 4 — Dashboard Q&A (account-level, $IDENTITY_REGION)"
    local cfg
    cfg=$(
        aws quicksight describe-dashboards-qa-configuration \
            --aws-account-id "$AWS_ACCOUNT" \
            --region "$IDENTITY_REGION" \
            --output json 2>&1
    ) || { red "  describe failed: $cfg"; return 1; }
    echo "$cfg" | sed 's/^/  /'
    if echo "$cfg" | grep -q '"DashboardsQAStatus": "ENABLED"'; then
        yellow "  Action: ./scripts/disable_quicksight_genai.sh disable  (or run manually with --dashboards-qa-status DISABLED)"
    else
        green "  OK — Dashboard Q&A DISABLED."
    fi
}

disable_step3_indexing() {
    bold "Disabling Step 3 — Q search / dashboard+visual indexing"
    aws quicksight update-quick-sight-q-search-configuration \
        --aws-account-id "$AWS_ACCOUNT" \
        --region "$IDENTITY_REGION" \
        --q-search-status DISABLED
    green "  Done."
}

disable_step4_dashboard_qa() {
    bold "Disabling Step 4 — Dashboard Q&A"
    aws quicksight update-dashboards-qa-configuration \
        --aws-account-id "$AWS_ACCOUNT" \
        --region "$IDENTITY_REGION" \
        --dashboards-qa-status DISABLED
    green "  Done."
}

audit_step5_users() {
    bold "Step 5 — User license count (Enterprise: ~\$24/mo per Author/Admin)"
    local users_json
    users_json=$(
        aws quicksight list-users \
            --aws-account-id "$AWS_ACCOUNT" \
            --namespace default \
            --region "$IDENTITY_REGION" \
            --output json 2>&1
    ) || { red "  list-users failed: $users_json"; return 1; }
    echo "$users_json" | python3 -c "
import json, sys
users = json.load(sys.stdin)['UserList']
from collections import Counter
roles = Counter(u['Role'] for u in users)
total_est = 0
for r, n in sorted(roles.items()):
    # Approx Enterprise prices; check current AWS pricing for exact.
    monthly = {'ADMIN':24,'AUTHOR':24,'READER':0,'ADMIN_PRO':50,'AUTHOR_PRO':50,'READER_PRO':20}.get(r,0)
    est = n * monthly
    total_est += est
    print(f'  {r:<15} {n:>3} user(s)   ~\${est:>4}/mo')
print(f'  {\"TOTAL est\":<15} {len(users):>3} user(s)   ~\${total_est:>4}/mo')
print()
print('  Detail:')
for u in users:
    print(f'    {u[\"Role\"]:<8}  {u[\"UserName\"]}')
"
    yellow "  Action: delete idle users with: aws quicksight delete-user --aws-account-id $AWS_ACCOUNT --namespace default --region $IDENTITY_REGION --user-name <name>"
}

audit_step6_vpc_connections() {
    bold "Step 6 — VPC connections (per-hour fee per connection)"
    local conns
    conns=$(
        aws quicksight list-vpc-connections \
            --aws-account-id "$AWS_ACCOUNT" \
            --region "$IDENTITY_REGION" \
            --query 'VPCConnectionSummaries[].{Id:VPCConnectionId,Name:Name,Status:Status}' \
            --output table 2>&1
    ) || { red "  list-vpc-connections failed: $conns"; return 1; }
    if echo "$conns" | grep -q VPCConnectionId; then
        red "  VPC connections found:"
        echo "$conns"
        yellow "  Action: delete with: aws quicksight delete-vpc-connection --aws-account-id $AWS_ACCOUNT --region $IDENTITY_REGION --vpc-connection-id <id>"
    else
        green "  OK — no VPC connections."
    fi
}

audit_step7_spice_capacity() {
    bold "Step 7 — SPICE capacity (provisioned even if unused, ~\$0.38/GB/mo above free tier)"
    local cap
    cap=$(
        aws quicksight describe-account-settings \
            --aws-account-id "$AWS_ACCOUNT" \
            --region "$IDENTITY_REGION" \
            --output json 2>&1
    ) || { yellow "  describe-account-settings failed: $cap"; return 1; }
    echo "$cap" | python3 -c "
import json, sys
try:
    s = json.load(sys.stdin)['AccountSettings']
    print(f'  Edition: {s.get(\"Edition\",\"?\")}   DefaultNamespace: {s.get(\"DefaultNamespace\",\"?\")}')
except Exception as e:
    print(f'  (parse failed: {e})')
"
    yellow "  SPICE consumed vs purchased is not exposed via CLI; check the console:"
    yellow "    https://us-east-1.quicksight.aws.amazon.com/sn/admin#capacity"
}

audit_step8_resource_sprawl() {
    bold "Step 8 — Resource sprawl (more resources → more indexed surface area)"
    local d a ds dsr
    d=$(aws quicksight list-dashboards --aws-account-id "$AWS_ACCOUNT" --region "$IDENTITY_REGION" --query 'length(DashboardSummaryList)' --output text 2>&1) || d="?"
    a=$(aws quicksight list-analyses  --aws-account-id "$AWS_ACCOUNT" --region "$IDENTITY_REGION" --query 'length(AnalysisSummaryList)'  --output text 2>&1) || a="?"
    ds=$(aws quicksight list-data-sets --aws-account-id "$AWS_ACCOUNT" --region "$IDENTITY_REGION" --query 'length(DataSetSummaries)'    --output text 2>&1) || ds="?"
    dsr=$(aws quicksight list-data-sources --aws-account-id "$AWS_ACCOUNT" --region "$IDENTITY_REGION" --query 'length(DataSources)'      --output text 2>&1) || dsr="?"
    printf '  %-15s %s\n' "Dashboards:"   "$d"
    printf '  %-15s %s\n' "Analyses:"     "$a"
    printf '  %-15s %s\n' "Datasets:"     "$ds"
    printf '  %-15s %s\n' "Datasources:"  "$dsr"
    if [[ "$d" != "0" || "$a" != "0" || "$ds" != "0" ]]; then
        yellow "  Action: sweep recon-gen-tagged resources with:"
        yellow "    cd run && recon-gen json clean --execute"
    else
        green "  OK — no leftover resources."
    fi
}

audit_step9_cost_history() {
    bold "Step 9 — QuickSight cost trend (last 30d, BlendedCost = amortized monthly subscription view)"
    local start end
    start=$(date -v-30d +%Y-%m-%d)
    end=$(date +%Y-%m-%d)
    aws ce get-cost-and-usage \
        --time-period "Start=$start,End=$end" \
        --granularity DAILY \
        --metrics BlendedCost UnblendedCost \
        --filter '{"Dimensions":{"Key":"SERVICE","Values":["Amazon QuickSight"]}}' \
        --group-by Type=DIMENSION,Key=USAGE_TYPE \
        --output json 2>&1 | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
except Exception as e:
    print(f'  (CE call failed — needs ce:GetCostAndUsage)')
    sys.exit(0)
from collections import defaultdict
by_type = defaultdict(lambda: {'blended':0.0,'unblended':0.0})
for period in data['ResultsByTime']:
    for g in period['Groups']:
        ut = g['Keys'][0]
        by_type[ut]['blended']   += float(g['Metrics']['BlendedCost']['Amount'])
        by_type[ut]['unblended'] += float(g['Metrics']['UnblendedCost']['Amount'])
rows = sorted(by_type.items(), key=lambda x: -abs(x[1]['blended']))
print(f'  {\"Usage type\":<55} {\"Blended (30d)\":>14} {\"Unblended (30d)\":>16}')
for ut, m in rows:
    if abs(m['blended']) < 0.001 and abs(m['unblended']) < 0.001: continue
    print(f'  {ut:<55} \${m[\"blended\"]:>+13.2f} \${m[\"unblended\"]:>+15.2f}')
total_b = sum(m['blended'] for _,m in rows)
total_u = sum(m['unblended'] for _,m in rows)
print(f'  {\"TOTAL\":<55} \${total_b:>+13.2f} \${total_u:>+15.2f}')
print()
print('  (Unblended = actual cash after credits. Blended = amortized monthly view.)')
print('  (Sustained nonzero UNBLENDED after credits exhaust = real bill.)')
"
}

cmd_audit() {
    bold "QuickSight cost audit"
    bold "  Account: $AWS_ACCOUNT   Profile: $AWS_PROFILE   Identity region: $IDENTITY_REGION"
    echo
    bold "--- Generative BI opt-out (steps 1-4) ---"
    audit_step1_pro_users;     echo
    audit_step2_topics;        echo
    audit_step3_indexing;      echo
    audit_step4_dashboard_qa;  echo
    bold "--- Other QuickSight cost surfaces (steps 5-8) ---"
    audit_step5_users;         echo
    audit_step6_vpc_connections; echo
    audit_step7_spice_capacity;  echo
    audit_step8_resource_sprawl; echo
    bold "--- Billing trend (step 9) ---"
    audit_step9_cost_history
}

cmd_disable() {
    cmd_audit
    echo
    bold "=== Disabling account-level toggles (steps 3 + 4) ==="
    bold "Steps 1 (Pro users) + 2 (Topics) are NOT auto-deleted — review the audit above and act manually."
    echo
    disable_step3_indexing; echo
    disable_step4_dashboard_qa
}

cmd_verify() {
    bold "Re-running audit to verify…"
    echo
    cmd_audit
}

cmd="${1:-audit}"
case "$cmd" in
    audit)   cmd_audit ;;
    disable) cmd_disable ;;
    verify)  cmd_verify ;;
    *)
        echo "Usage: $0 [audit|disable|verify]"
        exit 2
        ;;
esac
