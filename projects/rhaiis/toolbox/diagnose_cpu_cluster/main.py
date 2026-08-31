#!/usr/bin/env python3
"""Diagnose OpenShift cluster suitability for CPU vLLM benchmarking.

Run before deploying to verify node resources, CPU instruction sets,
NUMA topology, CPU manager policy, and KServe CRD availability.

Optionally apply rhaiis.io/* node labels for CPU scheduling (see --apply-labels).
"""

from __future__ import annotations

import json

from projects.core.dsl import (
    entrypoint,
    execute_tasks,
    shell,
    task,
)
from projects.core.dsl.utils.k8s import oc, oc_resource_exists
from projects.rhaiis.toolbox.diagnose_cpu_cluster.node_labels import (
    DEFAULT_BENCHMARK_NODE_SELECTOR,
    LABEL_CPU_BENCHMARK,
    compute_node_labels,
    is_worker_node,
    parse_cpu_cores,
    parse_cpu_flags,
)


@entrypoint
def run(
    *,
    apply_labels: bool = False,
    dry_run: bool = False,
    min_benchmark_cpu: float = 8,
    workers_only: bool = True,
):
    """Diagnose OpenShift cluster suitability for CPU vLLM benchmarking.

    Args:
        apply_labels: When True, apply rhaiis.io/* labels to worker nodes.
        dry_run: With apply_labels, print oc label commands without running them.
        min_benchmark_cpu: Minimum allocatable CPU cores for cpu-benchmark label.
        workers_only: Skip control-plane nodes for checks and labeling.
    """
    return execute_tasks(locals())


@task
def show_node_resources(args, context):
    nodes_result = shell.run("oc get nodes -o json", check=False)
    context.nodes = []
    context.node_allocatable_cpu: dict[str, float] = {}
    context.node_labels: dict[str, dict[str, str]] = {}

    if nodes_result.returncode != 0:
        print(nodes_result.stderr or nodes_result.stdout)
        raise RuntimeError("Failed to list cluster nodes")

    payload = json.loads(nodes_result.stdout)
    rows = []
    for item in payload.get("items", []):
        name = item["metadata"]["name"]
        labels = item.get("metadata", {}).get("labels", {})
        if args.workers_only and not is_worker_node(labels):
            continue

        status = item.get("status", {})
        alloc = status.get("allocatable", {})
        cpu = alloc.get("cpu", "?")
        mem = alloc.get("memory", "?")
        ready = next(
            (
                c.get("type")
                for c in status.get("conditions", [])
                if c.get("type") == "Ready" and c.get("status") == "True"
            ),
            "NotReady",
        )
        rows.append(f"{name}\t{cpu}\t{mem}\t{ready}")
        context.nodes.append(name)
        context.node_labels[name] = labels
        if cpu != "?":
            context.node_allocatable_cpu[name] = parse_cpu_cores(cpu)

    print("NAME\tCPU\tMEM\tSTATUS")
    print("\n".join(rows))
    return f"Node resources listed ({len(context.nodes)} worker node(s))"


@task
def check_cpu_instruction_sets(args, context):
    context.node_features: dict[str, dict] = {}
    for node in context.nodes:
        flags_result = shell.run(
            f"oc debug node/{node} -- chroot /host sh -c "
            "'grep -m1 flags /proc/cpuinfo 2>/dev/null'",
            check=False,
        )
        stdout = flags_result.stdout
        flags_line = next(
            (line for line in stdout.splitlines() if "flags" in line),
            "",
        )
        features = parse_cpu_flags(flags_line)
        context.node_features[node] = features
        avx2 = "YES" if features["avx2"] else "no"
        avx512 = "YES" if features["avx512"] else "no"
        amx = "YES" if features["amx"] else "no"
        print(f"  {node}: AVX2={avx2}  AVX-512={avx512}  AMX={amx}")
    return f"CPU instruction sets checked for {len(context.nodes)} node(s)"


@task
def check_numa_topology(args, context):
    for node in context.nodes:
        numa_result = shell.run(
            f"oc debug node/{node} -- chroot /host numactl --hardware",
            check=False,
        )
        available_line = next(
            (line for line in numa_result.stdout.splitlines() if line.startswith("available:")),
            "unknown",
        )
        print(f"  {node}: {available_line}")
    return f"NUMA topology checked for {len(context.nodes)} node(s)"


@task
def check_cpu_manager_policy(args, context):
    for node in context.nodes:
        state_result = shell.run(
            f"oc debug node/{node} -- chroot /host "
            "cat /var/lib/kubelet/cpu_manager_state",
            check=False,
        )
        policy = "unknown"
        try:
            stdout = state_result.stdout
            start = stdout.find("{")
            end = stdout.rfind("}") + 1
            if start >= 0 and end > start:
                policy = json.loads(stdout[start:end]).get("policyName", "unknown")
        except (json.JSONDecodeError, AttributeError, ValueError):
            policy = "unknown"
        static = policy == "static"
        context.node_features.setdefault(node, {})["cpu_manager_static"] = static
        print(f"  {node}: cpuManagerPolicy={policy}")
    return f"CPU manager policy checked for {len(context.nodes)} node(s)"


@task
def apply_node_labels(args, context):
    if not args.apply_labels:
        print("  (skipped — pass --apply-labels to write rhaiis.io/* node labels)")
        return "Node labeling skipped (diagnose-only mode)"

    planned: list[tuple[str, dict[str, str]]] = []
    for node in context.nodes:
        features = context.node_features.get(node, {})
        labels = compute_node_labels(
            avx2=features.get("avx2", False),
            avx512=features.get("avx512", False),
            amx=features.get("amx", False),
            cpu_manager_static=features.get("cpu_manager_static", False),
            allocatable_cpu_cores=context.node_allocatable_cpu.get(node, 0),
            min_benchmark_cpu=args.min_benchmark_cpu,
        )
        planned.append((node, labels))

    if not planned:
        raise RuntimeError("No worker nodes found to label")

    for node, labels in planned:
        if not labels:
            print(f"  {node}: no rhaiis labels (missing AVX2 or insufficient CPU)")
            continue
        label_args = " ".join(f"{key}={value}" for key, value in sorted(labels.items()))
        cmd = f"oc label node {node} {label_args} --overwrite"
        if args.dry_run:
            print(f"  [dry-run] {cmd}")
            continue
        result = oc("label", "node", node, *[
            f"{key}={value}" for key, value in sorted(labels.items())
        ], "--overwrite", check=False)
        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to label node {node}: {result.stderr or result.stdout}"
            )
        print(f"  {node}: {', '.join(sorted(labels.keys()))}")

    mode = "dry-run" if args.dry_run else "applied"
    benchmark_nodes = sum(
        1 for _, labels in planned if labels.get(LABEL_CPU_BENCHMARK) == "true"
    )
    print(
        f"\n  Benchmark selector {DEFAULT_BENCHMARK_NODE_SELECTOR} matches "
        f"{benchmark_nodes}/{len(planned)} worker node(s)"
    )
    return f"Node labels {mode} on {len(planned)} worker node(s)"


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
    print(
        f"  Deploy nodeSelector default for CPU presets: "
        f"{DEFAULT_BENCHMARK_NODE_SELECTOR}"
    )
    return "vLLM CPU image references listed"


if __name__ == "__main__":
    run.main()
