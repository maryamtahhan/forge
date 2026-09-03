import logging
import os

from projects.core.dsl.utils.k8s import oc
from projects.rhaiis.orchestration import runtime_config

logger = logging.getLogger(__name__)


def prepare():
    ns = runtime_config.get_namespace()
    deploy_cfg = runtime_config.get_deploy_config()
    logger.info(f"Preparing namespace {ns} for rhaiis benchmarks")

    result = oc("whoami", check=False)
    if result.returncode != 0:
        raise RuntimeError("Cannot connect to cluster")
    logger.info(f"Connected to cluster as {result.stdout.strip()}")

    result = oc("get", "namespace", ns, check=False)
    if result.returncode != 0:
        oc("create", "namespace", ns)
        logger.info(f"Created namespace {ns}")
    else:
        logger.info(f"Namespace {ns} already exists")

    sa_name = deploy_cfg.get("service_account_name", "")
    if sa_name:
        result = oc("get", "serviceaccount", sa_name, "-n", ns, check=False)
        if result.returncode != 0:
            oc("create", "serviceaccount", sa_name, "-n", ns)
            logger.info(f"Created service account {sa_name}")
        else:
            logger.info(f"Service account {sa_name} already exists")

    secret_name = deploy_cfg.get("image_pull_secret", "")
    if secret_name:
        result = oc("get", "secret", secret_name, "-n", ns, check=False)
        if result.returncode == 0:
            logger.info(f"Image pull secret {secret_name} exists")
        else:
            logger.warning(
                f"Image pull secret {secret_name} not found in {ns} — "
                "deployment may fail if images require authentication"
            )


def _delete_resources_by_suffix(resource_type: str, ns: str, suffix: str) -> None:
    """Delete resources whose names end with the FJOB suffix."""
    result = oc("get", resource_type, "-n", ns, "-o", "name", check=False, log_stdout=False)
    if result.returncode != 0 or not result.stdout:
        return
    for line in result.stdout.strip().splitlines():
        name = line.strip()
        if name.endswith(f"-{suffix}"):
            oc("delete", name, "-n", ns, "--ignore-not-found", check=False)


def cleanup():
    ns = runtime_config.get_namespace()
    logger.info(f"Cleaning up rhaiis benchmark resources in {ns}")

    fjob = os.environ.get("FJOB_NAME", "")
    if fjob:
        suffix = fjob.rsplit("-", 1)[-1]
        for resource_type in ("inferenceservice", "servingruntime", "job", "pod", "pvc"):
            _delete_resources_by_suffix(resource_type, ns, suffix)
    else:
        oc("delete", "inferenceservice", "--all", "-n", ns, "--ignore-not-found", check=False)
        oc("delete", "servingruntime", "--all", "-n", ns, "--ignore-not-found", check=False)
        oc("delete", "job", "--all", "-n", ns, "--ignore-not-found", check=False)
        oc("delete", "pod", "--all", "-n", ns, "--ignore-not-found", check=False)

    logger.info("Cleanup complete")
