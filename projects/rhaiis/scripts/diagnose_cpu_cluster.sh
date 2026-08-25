#!/usr/bin/env bash
# Diagnose OpenShift cluster suitability for CPU vLLM benchmarking.
# Run before deploying: bash projects/rhaiis/scripts/diagnose_cpu_cluster.sh

set -euo pipefail

echo "=== RHAIIS CPU Cluster Diagnostic ==="
echo

# Node resources
echo "--- Node Resources ---"
oc get nodes -o custom-columns=\
"NAME:.metadata.name,CPU:.status.allocatable.cpu,MEM:.status.allocatable.memory,STATUS:.status.conditions[-1].type"
echo

# CPU instruction set support
echo "--- CPU Instruction Set Support ---"
for node in $(oc get nodes -o jsonpath='{.items[*].metadata.name}'); do
    echo -n "  $node: "
    flags=$(oc debug node/"$node" -- chroot /host sh -c "grep -m1 flags /proc/cpuinfo 2>/dev/null" 2>/dev/null | grep -o "flags.*" || echo "")
    avx2="no"; avx512="no"; amx="no"
    echo "$flags" | grep -q " avx2 " && avx2="YES"
    echo "$flags" | grep -q " avx512f " && avx512="YES"
    echo "$flags" | grep -q " amx_tile " && amx="YES"
    echo "AVX2=$avx2  AVX-512=$avx512  AMX=$amx"
done
echo

# NUMA topology
echo "--- NUMA Topology ---"
for node in $(oc get nodes -o jsonpath='{.items[*].metadata.name}'); do
    echo -n "  $node: "
    numactl=$(oc debug node/"$node" -- chroot /host numactl --hardware 2>/dev/null | grep -E "^available:" || echo "unknown")
    echo "$numactl"
done
echo

# CPU Manager policy
echo "--- CPU Manager Policy ---"
for node in $(oc get nodes -o jsonpath='{.items[*].metadata.name}'); do
    echo -n "  $node: "
    policy=$(oc debug node/"$node" -- chroot /host sh -c \
        "cat /var/lib/kubelet/cpu_manager_state 2>/dev/null | python3 -c \"import sys,json; d=json.load(sys.stdin); print(d.get('policyName','unknown'))\" 2>/dev/null" \
        2>/dev/null || echo "unknown")
    echo "cpuManagerPolicy=$policy"
done
echo

# KServe CRDs
echo "--- KServe CRDs ---"
for crd in inferenceservices.serving.kserve.io servingruntimes.serving.kserve.io; do
    if oc get crd "$crd" &>/dev/null; then
        echo "  $crd: INSTALLED"
    else
        echo "  $crd: MISSING"
    fi
done
echo

# Image pull capability
echo "--- vLLM CPU Images ---"
echo "  RHAIIS:  registry.redhat.io/rhaii/vllm-cpu-rhel9:3.5.0-1786546771"
echo "  Vanilla: docker.io/vllm/vllm-openai-cpu:v0.25.1"
echo "  (pull test requires image pull secret for RHAIIS image)"
echo

echo "=== Diagnostic complete ==="
