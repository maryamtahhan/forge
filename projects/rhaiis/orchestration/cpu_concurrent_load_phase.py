from __future__ import annotations

import logging

from projects.core.library import config, env
from projects.core.library.postprocess import run_and_postprocess
from projects.rhaiis.orchestration.test_phase import _run_test

logger = logging.getLogger(__name__)

DEFAULT_MODEL_KEYS = ["tinyllama-cpu"]
DEFAULT_CPU_REQUESTS = ["8", "16", "32"]
DEFAULT_WORKLOAD_KEYS = ["cpu-chat-baseline"]


def run(
    *,
    model_keys: list[str] | None = None,
    cpu_requests: list[str] | None = None,
    workload_keys: list[str] | None = None,
    namespace: str,
    continue_on_error: bool = False,
) -> int:
    return run_and_postprocess(
        do_test,
        model_keys=model_keys,
        cpu_requests=cpu_requests,
        workload_keys=workload_keys,
        namespace=namespace,
        continue_on_error=continue_on_error,
    )


def do_test(
    *,
    model_keys: list[str] | None = None,
    cpu_requests: list[str] | None = None,
    workload_keys: list[str] | None = None,
    namespace: str,
    continue_on_error: bool = False,
) -> int:
    config.project.set_config("rhaiis.accelerator", "cpu")

    resolved_models = model_keys or DEFAULT_MODEL_KEYS
    resolved_cpu_requests = cpu_requests or DEFAULT_CPU_REQUESTS
    resolved_workloads = workload_keys or DEFAULT_WORKLOAD_KEYS

    total = len(resolved_models) * len(resolved_cpu_requests) * len(resolved_workloads)
    current = 0
    failed = 0

    for model_key in resolved_models:
        for cpu_request in resolved_cpu_requests:
            for workload_key in resolved_workloads:
                current += 1
                label = f"{model_key}_{cpu_request}cpu_{workload_key}"
                logger.info("[%d/%d] Running cell: %s", current, total, label)

                with env.NextArtifactDir(label):
                    try:
                        _run_test(
                            model_key=model_key,
                            workload_keys=[workload_key],
                            namespace=namespace,
                            deploy_cfg_overrides={"cpu_request": cpu_request},
                        )
                    except Exception:
                        failed += 1
                        logger.error("Cell %s failed", label, exc_info=True)
                        if not continue_on_error:
                            return 1

    if failed:
        logger.error("%d/%d cells failed", failed, total)
        return 1

    return 0
