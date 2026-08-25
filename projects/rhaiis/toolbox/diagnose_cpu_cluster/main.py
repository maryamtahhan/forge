#!/usr/bin/env python3
"""Diagnose OpenShift cluster suitability for CPU vLLM benchmarking.

Run before deploying to verify node resources, CPU instruction sets,
NUMA topology, CPU manager policy, and KServe CRD availability.
"""

from __future__ import annotations

import json

from projects.core.dsl import (
    entrypoint,
    execute_tasks,
    shell,
    task,
)
from projects.core.dsl.utils.k8s import oc_resource_exists


def _get_node_names() -> list[str]:
    result = shell.run(
        "oc get nodes -o jsonpath='{.items[*].metadata.name}'",
        check=False,
    )
    return result.stdout.strip().strip("'").split()


@entrypoint
def run():
    """Diagnose OpenShift cluster suitability for CPU vLLM benchmarking."""
    return execute_tasks(locals())


@task
def show_node_resources(args, context):
    result = shell.run(
        "oc get nodes -o custom-columns="
        "NAME:.metadata.name,"
        "CPU:.status.allocatable.cpu,"
        "MEM:.status.allocatable.memory,"
        "STATUS:.status.conditions[-1].type",
        check=False,
    )
    print(result.stdout)
    return "Node resources listed"


@task
def check_cpu_instruction_sets(args, context):
    nodes = _get_node_names()
    for node in nodes:
        flags_result = shell.run(
            f"oc debug node/{node} -- chroot /host sh -c "
            "'grep -m1 flags /proc/cpuinfo 2>/dev/null'",
            check=False,
        )
        flags = next(
            (line for line in flags_result.stdout.splitlines() if line.startswith("flags")),
            "",
        )
        avx2 = "YES" if " avx2 " in flags else "no"
        avx512 = "YES" if " avx512f " in flags else "no"
        amx = "YES" if " amx_tile " in flags else "no"
        print(f"  {node}: AVX2={avx2}  AVX-512={avx512}  AMX={amx}")
    return f"CPU instruction sets checked for {len(nodes)} node(s)"


@task
def check_numa_topology(args, context):
    nodes = _get_node_names()
    for node in nodes:
        numa_result = shell.run(
            f"oc debug node/{node} -- chroot /host numactl --hardware",
            check=False,
        )
        available_line = next(
            (line for line in numa_result.stdout.splitlines() if line.startswith("available:")),
            "unknown",
        )
        print(f"  {node}: {available_line}")
    return f"NUMA topology checked for {len(nodes)} node(s)"


@task
def check_cpu_manager_policy(args, context):
    nodes = _get_node_names()
    for node in nodes:
        state_result = shell.run(
            f"oc debug node/{node} -- chroot /host "
            "cat /var/lib/kubelet/cpu_manager_state",
            check=False,
        )
        try:
            policy = json.loads(state_result.stdout).get("policyName", "unknown")
        except (json.JSONDecodeError, AttributeError):
            policy = "unknown"
        print(f"  {node}: cpuManagerPolicy={policy}")
    return f"CPU manager policy checked for {len(nodes)} node(s)"


@task
def check_kserve_crds(args, context):
    crds = [
        "inferenceservices.serving.kserve.io",
        "servingruntimes.serving.kserve.io",
    ]
    missing = []
    for crd in crds:
        status = "INSTALLED" if oc_resource_exists("crd", crd) else "MISSING"
        if status == "MISSING":
            missing.append(crd)
        print(f"  {crd}: {status}")
    if missing:
        context.missing_crds = missing
    return f"KServe CRDs: {len(crds) - len(missing)}/{len(crds)} installed"


@task
def show_cpu_images(args, context):
    print("  RHAIIS:  registry.redhat.io/rhaii/vllm-cpu-rhel9:3.5.0-1786546771")
    print("  Vanilla: docker.io/vllm/vllm-openai-cpu:v0.25.1")
    print("  (pull test requires image pull secret for RHAIIS image)")
    return "vLLM CPU image references listed"


if __name__ == "__main__":
    run.main()
