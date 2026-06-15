#!/usr/bin/env bash
# MUSTER one-command reproducer.
#
# Re-derives the core claims of the submission WITHOUT reading any code:
#   1. re-derives the Band Contract-Net coordination keystone offline, from
#      committed spike evidence, with NO Band credentials (announce→recruit→bid
#      →award across distinct external Band identities; 3 runtimes, 1 rule),
#   2. brings up the real kind cluster + the shop/billing sample apps + RBAC,
#   3. runs the naive-vs-hardened contrast on the SAME injected fault
#      (expect: naive blast=2 touches billing & scales-to-zero unapproved;
#       hardened blast=0, billing untouched, destructive op gated, recovered),
#   4. proves the shop/billing boundary is enforced by the Kubernetes API
#      server (not by Python) via a ServiceAccount self-test.
#
# Usage (from cases/band-of-agents/):   bash scripts/demo.sh
# Idempotent: re-running reuses the cluster. Cluster invariant: every run
# restores the cluster to baseline afterward.
#
# Prereqs on PATH: docker, kind, kubectl, uv (https://github.com/astral-sh/uv).
# No Band credentials are needed for this reproducer — the coordination keystone
# is re-derived from committed evidence (stage 1), and stages 2-5 exercise the
# real cluster + remediation tool layer + RBAC. (The live observatory replays
# the SAME coordination against app.band.ai in real time; see README.)
set -euo pipefail

CLUSTER=muster
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$HERE/app"

banner() { printf '\n\033[1m=== %s ===\033[0m\n' "$1"; }

banner "0/5  toolchain"
for bin in docker kind kubectl uv; do
  command -v "$bin" >/dev/null 2>&1 || { echo "missing required tool: $bin" >&2; exit 1; }
  printf '  %-8s %s\n' "$bin" "$(command -v "$bin")"
done

banner "1/5  Band coordination keystone — offline, no Band credentials"
# Re-derives the FIPA Contract-Net coordination claim from committed spike
# evidence; exits non-zero if any coordination invariant fails. Needs only a
# stdlib Python — runs before the cluster is even up.
SYS_PY=""
for cand in python3 python py; do
  p="$(command -v "$cand" 2>/dev/null || true)"
  [ -n "$p" ] || continue
  "$p" -c 'import sys' >/dev/null 2>&1 || continue   # skip Store stubs / shims
  SYS_PY="$p"; break
done
[ -n "$SYS_PY" ] || { echo "missing required tool: a working python3" >&2; exit 1; }
PYTHONUTF8=1 "$SYS_PY" "$HERE/spikes/verify_coordination.py" "$HERE/spikes"

banner "2/5  cluster + sample apps + RBAC"
if kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  echo "  kind cluster '$CLUSTER' already up — reusing"
else
  kind create cluster --name "$CLUSTER" --config "$APP/cluster/kind-config.yaml"
fi
kubectl --context "kind-$CLUSTER" apply -f "$APP/cluster/manifests/"
echo "  waiting for shop workloads to become ready..."
kubectl --context "kind-$CLUSTER" -n shop wait --for=condition=available \
  deploy --all --timeout=180s

banner "3/5  python deps (uv)"
cd "$APP"
uv venv >/dev/null 2>&1 || true
uv sync
PY="$APP/.venv/bin/python"
[ -x "$PY" ] || PY="$APP/.venv/Scripts/python.exe"   # Windows layout fallback

export PYTHONUTF8=1
export PYTHONPATH="$APP:$APP/agents"   # naive/cluster live under app/, common under app/agents/
export MUSTER_KUBECTL="$(command -v kubectl)"
export MUSTER_CONTEXT="kind-$CLUSTER"

banner "4/5  naive vs hardened on the SAME real fault (受賞関数)"
# exits non-zero unless the contrast holds (naive blows up, hardened does not)
"$PY" -m naive.control_agent compare

banner "5/5  RBAC boundary is enforced by the Kubernetes API server"
# expect boundary_holds: true (shop patch ok; billing read 403 Forbidden as SA)
"$PY" -m common.k8s rbac

banner "DONE"
echo "The Band Contract-Net coordination keystone (offline), the naive↔hardened"
echo "blast-radius contrast, and the API-enforced shop/billing boundary were all"
echo "re-derived from scratch by this script — no Band credentials required."
