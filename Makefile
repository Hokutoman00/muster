# MUSTER — convenience targets. The reproducer of record is scripts/demo.sh.
.PHONY: demo up down observatory rbac compare

# One command: cluster + apps + RBAC, naive-vs-hardened contrast, RBAC proof.
demo:
	bash scripts/demo.sh

# Bring the real cluster + sample apps + RBAC up (no measurement).
up:
	kind create cluster --name muster --config app/cluster/kind-config.yaml
	kubectl --context kind-muster apply -f app/cluster/manifests/
	kubectl --context kind-muster -n shop wait --for=condition=available deploy --all --timeout=180s

# Tear the cluster down.
down:
	kind delete cluster --name muster

# Just the naive-vs-hardened contrast (assumes `make up` already ran).
compare:
	cd app && PYTHONUTF8=1 PYTHONPATH="$$PWD:$$PWD/agents" MUSTER_KUBECTL="$$(command -v kubectl)" \
	  .venv/bin/python -m naive.control_agent compare

# Just the API-server-enforced RBAC self-test.
rbac:
	cd app && PYTHONUTF8=1 PYTHONPATH="$$PWD:$$PWD/agents" MUSTER_KUBECTL="$$(command -v kubectl)" \
	  .venv/bin/python -m common.k8s rbac

# Re-derive the BAND COORDINATION claim (not just the cluster/RBAC contrast):
# runs the real Contract-Net cycle on real Band against the real cluster —
# discover -> signature shortlist -> announce CFP -> recruit -> competing bids ->
# select_award -> @mention -> de-muster loser -> winner executes -> cluster recovers.
# Requires the Band agent creds: point BAND_ENV at your creds file (see README/Configuration).
# Writes spikes/p4-commander.evidence.json with the full transcript.
coordination-demo:
	cd app && PYTHONUTF8=1 PYTHONPATH="$$PWD:$$PWD/agents" MUSTER_KUBECTL="$$(command -v kubectl)" \
	  .venv/bin/python ../spikes/p4_commander_loop.py

# Launch the live observatory (needs the 4 Band agent creds in the env; see README).
observatory:
	cd app/observatory/backend && PYTHONUTF8=1 MUSTER_KUBECTL="$$(command -v kubectl)" \
	  ../../.venv/bin/python -m uvicorn server:app --host 0.0.0.0 --port 8080
