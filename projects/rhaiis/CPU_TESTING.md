# CPU Accelerator Testing Guide

This document covers testing CPU inference with vLLM on an OpenShift cluster, supporting
both upstream vLLM CPU builds (vanilla) and the Red Hat AI Inference Service (RHAIIS)
CPU image.

## Cluster Setup

### 1. Log in to OpenShift

```bash
oc login --token=<token> --server=<server> --insecure-skip-tls-verify=true
```

### 2. Diagnose the cluster

Run the diagnostic script to verify CPU instruction sets, KServe availability,
and image pull capability before committing to a namespace:

```bash
bash projects/rhaiis/scripts/diagnose_cpu_cluster.sh
```

The script checks:
- Node resources (CPU, memory allocatable)
- AVX2 support (required) and AVX-512 (optional, ~2-3x faster if present)
- NUMA topology
- CPU Manager policy (static = dedicated CPUs; none = time-sliced)
- KServe CRD installation

### 3. Create the namespace

```bash
oc new-project forge-rhaiis
oc label namespace forge-rhaiis opendatahub.io/dashboard=true
```

### 4. Create secrets

HuggingFace token (required — the secret must exist even for ungated models):

```bash
oc create secret generic storage-config \
  --from-literal=HF_TOKEN=<your-hf-token> \
  -n forge-rhaiis
```

RHAIIS image pull secret (only needed when using `--cpu-flavor rhaiis`):

```bash
oc create secret docker-registry rhaiis-pull-secret \
  --docker-server=registry.redhat.io \
  --docker-username=<user> --docker-password=<token> \
  -n forge-rhaiis
```

### 5. Set artifact directory

```bash
export ARTIFACT_DIR=/tmp/rhaiis-artifacts
mkdir -p $ARTIFACT_DIR
```

## Single-Run Tests

### Dry-run (no cluster required)

```bash
# Vanilla upstream vLLM CPU
python -m projects.rhaiis.orchestration.cli test \
  --accelerator cpu --cpu-flavor vanilla \
  --model tinyllama-cpu --workload cpu-smoke \
  --namespace forge-rhaiis \
  --dry-run

# RHAIIS CPU
python -m projects.rhaiis.orchestration.cli test \
  --accelerator cpu --cpu-flavor rhaiis \
  --model tinyllama-cpu --workload cpu-smoke \
  --namespace forge-rhaiis \
  --dry-run
```

### Smoke test (cluster required)

```bash
# Vanilla (no image pull secret needed)
python -m projects.rhaiis.orchestration.cli test \
  --accelerator cpu --cpu-flavor vanilla \
  --model tinyllama-cpu --workload cpu-smoke \
  --namespace forge-rhaiis

# RHAIIS
python -m projects.rhaiis.orchestration.cli test \
  --accelerator cpu --cpu-flavor rhaiis \
  --model tinyllama-cpu --workload cpu-smoke \
  --namespace forge-rhaiis \
  --image-pull-secret rhaiis-pull-secret
```

### Baseline workloads

```bash
python -m projects.rhaiis.orchestration.cli test \
  --accelerator cpu --cpu-flavor vanilla \
  --model llama31-8b-w8a8-cpu \
  --workload cpu-chat-baseline \
  --namespace forge-rhaiis
```

## Concurrent Load Matrix

The concurrent load test sweeps `models × cpu_requests × workloads`, matching
the format-results `concurrent-load` suite.

### Run the matrix

```bash
python -m projects.rhaiis.orchestration.cli concurrent-load \
  --models tinyllama-cpu,qwen3-0-6b-cpu \
  --cpu-requests 8,16 \
  --workloads cpu-chat-baseline,cpu-rag-baseline \
  --namespace forge-rhaiis \
  --continue-on-error
```

`--cpu-flavor` defaults to `vanilla`. For RHAIIS:

```bash
python -m projects.rhaiis.orchestration.cli concurrent-load \
  --cpu-flavor rhaiis \
  --image-pull-secret rhaiis-pull-secret \
  --models llama31-8b-w8a8-cpu \
  --cpu-requests 8,16 \
  --workloads cpu-chat-baseline,cpu-code-baseline \
  --namespace forge-rhaiis
```

### Matrix dimensions

| Dimension | Default | Notes |
|---|---|---|
| `--models` | `tinyllama-cpu` | See CPU models below |
| `--cpu-requests` | `8,16,32` | Limit to 8,16 on nodes with <24 vCPUs |
| `--workloads` | `cpu-chat-baseline` | See CPU workloads below |
| `--cpu-flavor` | `vanilla` | `vanilla` or `rhaiis` |

### CPU models

| Key | Model | Notes |
|---|---|---|
| `tinyllama-cpu` | TinyLlama/TinyLlama-1.1B-Chat-v1.0 | Ungated, good for CI |
| `qwen3-0-6b-cpu` | Qwen/Qwen3-0.6B | Ungated, smallest |
| `llama-3-2-1b-cpu` | meta-llama/Llama-3.2-1B-Instruct | Ungated |
| `llama-3-2-3b-cpu` | meta-llama/Llama-3.2-3B-Instruct | Gated, requires HF_TOKEN |
| `granite-3-2-2b-cpu` | ibm-granite/granite-3.2-2b-instruct | Ungated |
| `llama31-8b-w8a8-cpu` | RedHatAI/Meta-Llama-3.1-8B-Instruct-quantized.w8a8 | Gated, RHAIIS production model |

### CPU workloads

| Key | ISL | OSL | Phase | Notes |
|---|---|---|---|---|
| `cpu-smoke` | 512 | 512 | — | CI-friendly, 120s, rates 1/2/4 |
| `cpu-chat-baseline` | 512 | 512 | 1 (fixed) | 600s, rates 1-32 |
| `cpu-rag-baseline` | 7680 | 512 | 1 (fixed) | 600s, rates 1-16 |
| `cpu-code-baseline` | 1024 | 1024 | 1 (fixed) | 600s, rates 1-32 |
| `cpu-summarization-baseline` | 2048 | 256 | 1 (fixed) | 600s, rates 1-16 |
| `cpu-chat-realistic` | 512±128 | 512±128 | 2 (variable) | No caching |
| `cpu-code-realistic` | 1024±256 | 1024±256 | 2 (variable) | No caching |

### vLLM images

| Flavor | Image |
|---|---|
| `vanilla` | `docker.io/vllm/vllm-openai-cpu:v0.25.1` |
| `rhaiis` | `registry.redhat.io/rhaii/vllm-cpu-rhel9:3.4.0` |

## Cleanup

```bash
python -m projects.rhaiis.orchestration.cli cleanup \
  --deployment-name <name> \
  --namespace forge-rhaiis
```

## Troubleshooting

### Pod stuck in Pending

```bash
oc describe pod -n forge-rhaiis -l serving.kserve.io/inferenceservice=<name> | grep -A 10 "Events:"
```

Common causes: insufficient CPU/memory on node, missing PVC.
Node has ~23.5 vCPUs on the lab cluster — limit `--cpu-requests` to `8,16`.

### `secret "storage-config" not found`

```bash
oc create secret generic storage-config \
  --from-literal=HF_TOKEN=<token> \
  -n forge-rhaiis
```

### vLLM slow to start

First startup compiles with Torch Inductor (~5-10 min on CPU without AVX-512).
Subsequent runs on the same node reuse the AOT cache and start much faster.

### `oneDNN linear fallback` warning

Expected on CPUs without AVX-512 (e.g. Haswell E5-2620 v3). Performance impact
but functionally correct.

## Known Limitations

- **No bare-metal support**: CPU testing in forge is OpenShift/KServe-only.
  For bare-metal NUMA/cpuset testing, use the format-results Ansible playbooks.
- **No CPU Manager pinning**: CPU Manager must be enabled on the cluster with
  static policy for dedicated CPU allocation. Without it, CPUs are time-sliced
  and benchmark results will have higher variance.
- **No Phase 3**: Prefix-caching (Phase 3) workloads are defined in config but
  not yet part of the concurrent load suite (matching format-results).
