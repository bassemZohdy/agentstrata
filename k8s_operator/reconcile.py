"""AgentConfig reconciler (REQUIREMENTS.md K8S-01).

Pure functions: given an AgentConfig custom resource, compute the desired
ConfigMap (the tier-8 ``agent.yaml`` overlay the runtime watcher consumes),
Deployment, Service, and status. No kubernetes imports — the caller (the
operator loop or a test) supplies a ``KubeClient`` from ``kube.py``.

Conventions:
- names derive from the CR name: ``agentstrata-<cr-name>``.
- the ConfigMap data key is ``agent.yaml`` (matches the runtime watcher).
- every object carries ``app.kubernetes.io/managed-by: agentstrata-operator``
  and an ownerReference to the CR, so the Kubernetes GC removes them when
  the CR is deleted (no finalizer needed).
- the Deployment image comes from the ``agentstrata.io/image`` annotation
  (required); everything else mirrors manifests/deployment.yaml
  (non-root, read-only rootfs, drop ALL, probes, 35 s termination grace).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

OWNER_LABEL = "app.kubernetes.io/managed-by"
OWNER_VALUE = "agentstrata-operator"
API_GROUP = "agentstrata.io"
API_VERSION = "agentstrata.io/v1"
KIND = "AgentConfig"
IMAGE_ANNOTATION = "agentstrata.io/image"


def resource_name(cr_name: str) -> str:
    return f"agentstrata-{cr_name}"


def _owner_ref(cr: dict[str, Any]) -> dict[str, Any]:
    metadata = cr.get("metadata", {})
    return {
        "apiVersion": API_VERSION,
        "kind": KIND,
        "name": metadata.get("name", ""),
        "uid": metadata.get("uid", ""),
    }


def _labels(cr_name: str) -> dict[str, str]:
    return {
        OWNER_LABEL: OWNER_VALUE,
        "app.kubernetes.io/name": resource_name(cr_name),
    }


def desired_configmap(cr: dict[str, Any], overlay_yaml: str) -> dict[str, Any]:
    """K8S-01: the watched ConfigMap carrying the tier-8 overlay."""
    metadata = cr.get("metadata", {})
    cr_name = metadata.get("name", "")
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": resource_name(cr_name),
            "namespace": metadata.get("namespace", "default"),
            "labels": _labels(cr_name),
            "ownerReferences": [_owner_ref(cr)],
        },
        "data": {"agent.yaml": overlay_yaml},
    }


def desired_deployment(cr: dict[str, Any], image: str) -> dict[str, Any]:
    """K8S-01: the runtime Deployment pointed at the generated ConfigMap."""
    metadata = cr.get("metadata", {})
    cr_name = metadata.get("name", "")
    name = resource_name(cr_name)
    namespace = metadata.get("namespace", "default")
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": _labels(cr_name),
            "ownerReferences": [_owner_ref(cr)],
        },
        "spec": {
            "replicas": 1,  # SES-01: memory/file storage is single-replica
            "selector": {"matchLabels": {"app.kubernetes.io/name": name}},
            "template": {
                "metadata": {"labels": {"app.kubernetes.io/name": name, **_labels(cr_name)}},
                "spec": {
                    "terminationGracePeriodSeconds": 35,  # CNT-07
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": 10001,
                        "runAsGroup": 0,
                        "fsGroup": 0,
                    },
                    "containers": [
                        {
                            "name": "runtime",
                            "image": image,
                            "imagePullPolicy": "IfNotPresent",
                            "env": [
                                {"name": "AGENT_K8S_ENABLED", "value": "true"},
                                {
                                    "name": "AGENT_K8S_NAMESPACE",
                                    "valueFrom": {"fieldRef": {"fieldPath": "metadata.namespace"}},
                                },
                                {"name": "AGENT_K8S_NAME", "value": name},
                                {"name": "AGENT_CONFIG_DIR", "value": "/etc/agent"},
                            ],
                            "ports": [{"containerPort": 8080, "name": "http"}],
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "readOnlyRootFilesystem": True,
                                "capabilities": {"drop": ["ALL"]},
                            },
                            "resources": {
                                "requests": {"cpu": "100m", "memory": "128Mi"},
                                "limits": {"cpu": "2", "memory": "1Gi"},
                            },
                            "livenessProbe": {
                                "httpGet": {"path": "/healthz", "port": 8080},
                                "initialDelaySeconds": 5,
                                "periodSeconds": 10,
                            },
                            "readinessProbe": {
                                "httpGet": {"path": "/readyz", "port": 8080},
                                "initialDelaySeconds": 5,
                                "periodSeconds": 5,
                            },
                            "volumeMounts": [
                                {"name": "config", "mountPath": "/etc/agent", "readOnly": True},
                                {"name": "tmp", "mountPath": "/tmp"},
                            ],
                        }
                    ],
                    "volumes": [
                        {"name": "config", "configMap": {"name": name}},
                        {"name": "tmp", "emptyDir": {}},
                    ],
                },
            },
        },
    }


def desired_service(cr: dict[str, Any]) -> dict[str, Any]:
    metadata = cr.get("metadata", {})
    cr_name = metadata.get("name", "")
    name = resource_name(cr_name)
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": name,
            "namespace": metadata.get("namespace", "default"),
            "labels": _labels(cr_name),
            "ownerReferences": [_owner_ref(cr)],
        },
        "spec": {
            "selector": {"app.kubernetes.io/name": name},
            "ports": [{"port": 8080, "targetPort": 8080, "name": "http"}],
        },
    }


def build_status(
    cr: dict[str, Any],
    *,
    ready: bool,
    reason: str,
    message: str,
    applied_configmap: str | None = None,
    applied_resource_version: str | None = None,
) -> dict[str, Any]:
    generation = cr.get("metadata", {}).get("generation", 0)
    return {
        "observedGeneration": generation,
        "conditions": [
            {
                "type": "Ready",
                "status": "True" if ready else "False",
                "reason": reason,
                "message": message,
                "lastTransitionTime": datetime.now(UTC).isoformat(),
            }
        ],
        "appliedConfigMap": applied_configmap,
        "appliedConfigMapResourceVersion": applied_resource_version,
    }


async def reconcile(cr: dict[str, Any], kube: Any) -> dict[str, Any]:
    """K8S-01: reconcile ONE AgentConfig; returns the status to patch.

    Order: validate spec -> ensure ConfigMap -> ensure Deployment ->
    ensure Service -> status Ready=True. Any failure yields Ready=False
    with the reason; the loop retries on the next resync.
    """
    metadata = cr.get("metadata", {})
    cr_name = metadata.get("name", "")
    namespace = metadata.get("namespace", "default")
    spec = cr.get("spec") or {}

    # 1. validate the spec as an Agent Definition (same model the runtime
    #    uses; the CRD schema already rejects shape errors at admission).
    try:
        from app.config.models import AgentConfig as _ConfigModel

        _ConfigModel.model_validate(spec)
    except Exception as exc:  # noqa: BLE001 — any validation failure
        return build_status(
            cr, ready=False, reason="InvalidSpec", message=f"spec validation failed: {exc}"
        )

    # 2. the tier-8 overlay YAML the runtime watcher consumes.
    import yaml as _yaml

    try:
        overlay_yaml = _yaml.safe_dump(spec, sort_keys=False)
    except Exception as exc:  # noqa: BLE001
        return build_status(
            cr, ready=False, reason="SerializeError", message=f"overlay serialization failed: {exc}"
        )

    # 3. the Deployment image (annotation; fail closed when missing).
    image = (cr.get("metadata", {}).get("annotations") or {}).get(IMAGE_ANNOTATION, "")
    if not image:
        return build_status(
            cr,
            ready=False,
            reason="MissingImage",
            message=f"annotation {IMAGE_ANNOTATION} is required",
        )

    cm = desired_configmap(cr, overlay_yaml)
    await kube.apply_configmap(cm)
    latest = await kube.get_configmap(namespace, resource_name(cr_name))
    applied_rv = (latest or {}).get("resourceVersion", "")

    await kube.apply_deployment(desired_deployment(cr, image))
    await kube.apply_service(desired_service(cr))

    return build_status(
        cr,
        ready=True,
        reason="Applied",
        message="configmap/deployment/service reconciled",
        applied_configmap=resource_name(cr_name),
        applied_resource_version=applied_rv,
    )
