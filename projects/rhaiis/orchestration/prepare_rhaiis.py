import base64
import json
import logging
import os

from projects.core.dsl.utils.k8s import oc
from projects.core.library import vault
from projects.rhaiis.orchestration import runtime_config

logger = logging.getLogger(__name__)


def _ensure_storage_config_secret(ns: str) -> None:
    """Create storage-config secret with HF_TOKEN if it does not already exist."""
    result = oc("get", "secret", "storage-config", "-n", ns, check=False, log_stdout=False)
    if result.returncode == 0:
        logger.info("Secret storage-config already exists in %s", ns)
        return

    token_path = vault.get_vault_content_path("psap-forge-hf", "hf_token")
    if token_path is None or not token_path.exists():
        logger.warning(
            "psap-forge-hf vault not available — storage-config secret must be "
            "created manually in %s for HuggingFace model downloads to work",
            ns,
        )
        return

    # Apply via stdin with handled_secretly=True so the token never appears in
    # command args, logs, or artifacts (oc() suppresses all output for secret ops).
    token_b64 = base64.b64encode(token_path.read_text().strip().encode()).decode()
    manifest = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": "storage-config", "namespace": ns},
        "type": "Opaque",
        "data": {"HF_TOKEN": token_b64},
    }
    oc("apply", "-f", "-", input_text=json.dumps(manifest), handled_secretly=True)
    logger.info("Created storage-config secret in %s", ns)


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

    _ensure_storage_config_secret(ns)


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
