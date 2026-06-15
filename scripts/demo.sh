#!/usr/bin/env bash
# MUSTER one-command reproducer.
#
# Re-derives the core claims of the submission WITHOUT reading any code:
#   1. brings up the real kind cluster + the shop/billing sample apps + RBAC,
#   2. runs the naive-vs-hardened contrast on the SAME injected fault
#      (expect: naive blast=2 touches billing & scales-to-zero unapproved;
#       hardened blast=0, billing untouched, destructive op gated, recovered),
#   3. proves the shop/billing boundary is enforced by the Kubernetes API
#      server (not by Python) via a ServiceAccount self-test.
#
# Usage (from cases/band-of-agents/):   bash scripts/demo.sh
# Idempotent: re-running reuses the cluster. Cluster invariant: every run
# restores the cluster to baseline afterward.
#
# Prereqs on PATH: docker, kind, kubectl, uv (https://github.com/astral-sh/uv).
# No Band credentials are needed for this reproducer — it exercises the real
# cluster + remediation tool layer + RBAC. (The Band Contract-Net coordination
# layer is what the live observatory adds on top; see README.)
set -euo pipefail

CLUSTER=muster
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$HERE/app"

banner() { printf '\n\033[1m=== %s ===\033[0m\n' "$1"; }

banner "0/4  toolchain"
for bin in docker kind kubectl uv; do
  command -v "$bin" >/dev/null 2>&1 || { echo "missing required tool: $bin" >&2; exit 1; }
  printf '  %-8s %s\n' "$bin" "$(command -v "$bin")"
done

banner "1/4  cluster + sample apps + RBAC"
if kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  echo "  kind cluster '$CLUSTER' already up — reusing"
else
  kind create cluster --name "$CLUSTER" --config "$APP/cluster/kind-config.yaml"
fi
kubectl --context "kind-$CLUSTER" apply -f "$APP/cluster/manifests/"
echo "  waiting for shop workloads to become ready..."
kubectl --context "kind-$CLUSTER" -n shop wait --for=condition=available \
  deploy --all --timeout=180s

banner "2/4  python deps (uv)"
cd "$APP"
uv venv >/dev/null 2>&1 || true
uv sync
PY="$APP/.venv/bin/python"
[ -x "$PY" ] || PY="$APP/.venv/Scripts/python.exe"   # Windows layout fallback

export PYTHONUTF8=1
export PYTHONPATH="$APP:$APP/agents"   # naive/cluster live under app/, common under app/agents/
export MUSTER_KUBECTL="$(command -v kubectl)"
export MUSTER_CONTEXT="kind-$CLUSTER"

banner "3/4  naive vs hardened on the SAME real fault (受賞関数)"
# exits non-zero unless the contrast holds (naive blows up, hardened does not)
"$PY" -m naive.control_agent compare

banner "4/4  RBAC boundary is enforced by the Kubernetes API server"
# expect boundary_holds: true (shop patch ok; billing read 403 Forbidden as SA)
"$PY" -m common.k8s rbac

banner "DONE"
echo "Both the naive↔hardened blast-radius contrast and the API-enforced"
echo "shop/billing boundary were re-derived live, from scratch, by this script."
