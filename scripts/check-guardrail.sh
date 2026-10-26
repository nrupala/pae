#!/usr/bin/env bash
# Non-advisory guardrail gate (CONTRIBUTING rule 5): user-facing surfaces
# must never label output "optimal" or "recommended". Scans the surfaces a
# user or agent actually sees: the dashboard source, the MCP tool layer,
# and the product site. Docs that state the rule itself are out of scope.
set -euo pipefail
cd "$(dirname "$0")/.."
hits=$(grep -rniE '\boptimal\b|\brecommended\b' ui/src analytics/pae/mcp site \
  | grep -viE 'guardrail|forbidden|never|not optimal|no optimal' || true)
if [ -n "$hits" ]; then
  echo "GUARDRAIL BREACH: advice-implying label in a user-facing surface:"
  echo "$hits"
  exit 1
fi
echo "Guardrail check passed: no 'optimal'/'recommended' labels in user-facing surfaces."
