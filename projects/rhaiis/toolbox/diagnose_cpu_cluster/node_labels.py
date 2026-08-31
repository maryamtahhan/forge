"""CPU node label keys and helpers for rhaiis cluster diagnostics."""

from __future__ import annotations

LABEL_CPU_VLLM_CAPABLE = "rhaiis.io/cpu-vllm-capable"
LABEL_CPU_AVX512 = "rhaiis.io/cpu-avx512"
LABEL_CPU_AMX = "rhaiis.io/cpu-amx"
LABEL_CPU_MANAGER_STATIC = "rhaiis.io/cpu-manager-static"
LABEL_CPU_BENCHMARK = "rhaiis.io/cpu-benchmark"

DEFAULT_BENCHMARK_NODE_SELECTOR = {LABEL_CPU_BENCHMARK: "true"}

MANAGED_CPU_LABELS = (
    LABEL_CPU_VLLM_CAPABLE,
    LABEL_CPU_AVX512,
    LABEL_CPU_AMX,
    LABEL_CPU_MANAGER_STATIC,
    LABEL_CPU_BENCHMARK,
)


def parse_cpu_flags(cpuinfo_line: str) -> dict[str, bool]:
    """Parse /proc/cpuinfo flags line into feature booleans."""
    flags = f" {cpuinfo_line} "
    return {
        "avx2": " avx2 " in flags,
        "avx512": " avx512f " in flags,
        "amx": " amx_tile " in flags,
    }


def parse_cpu_cores(allocatable_cpu: str) -> float:
    """Parse Kubernetes allocatable CPU quantity to core count."""
    value = allocatable_cpu.strip()
    if value.endswith("m"):
        return int(value[:-1]) / 1000
    return float(value)


def is_worker_node(node_labels: dict[str, str]) -> bool:
    """Return True if the node is eligible for CPU benchmark workloads."""
    _excluded_roles = {
        "node-role.kubernetes.io/control-plane",
        "node-role.kubernetes.io/master",
        "node-role.kubernetes.io/infra",
    }
    return _excluded_roles.isdisjoint(node_labels)


def compute_node_labels(
    *,
    avx2: bool,
    avx512: bool,
    amx: bool,
    cpu_manager_static: bool,
    allocatable_cpu_cores: float,
    min_benchmark_cpu: float = 8,
) -> dict[str, str]:
    """Compute rhaiis.io/* labels from detected node capabilities."""
    labels: dict[str, str] = {}
    if avx2:
        labels[LABEL_CPU_VLLM_CAPABLE] = "true"
    if avx512:
        labels[LABEL_CPU_AVX512] = "true"
    if amx:
        labels[LABEL_CPU_AMX] = "true"
    if cpu_manager_static:
        labels[LABEL_CPU_MANAGER_STATIC] = "true"
    if avx2 and allocatable_cpu_cores >= min_benchmark_cpu:
        labels[LABEL_CPU_BENCHMARK] = "true"
    return labels


def find_managed_labels_on_node(node_labels: dict[str, str]) -> list[str]:
    """Return managed rhaiis.io CPU label keys present on a node."""
    return [key for key in MANAGED_CPU_LABELS if key in node_labels]
