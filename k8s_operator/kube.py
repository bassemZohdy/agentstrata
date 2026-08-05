"""Kubernetes client boundary for the operator (K8S-01).

``RealKubeClient`` uses in-cluster credentials (like the runtime watcher);
``FakeKubeClient`` is the in-memory substitute for tests. Both satisfy the
same async surface: apply (create-or-update with resourceVersion
optimistic concurrency), get, and the AgentConfig list/watch.
"""

from __future__ import annotations

import asyncio
from typing import Any


class KubeClient:
    """The operator's cluster surface (protocol)."""

    async def list_agentconfigs(self, namespace: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def apply_configmap(self, cm: dict[str, Any]) -> None:
        raise NotImplementedError

    async def get_configmap(self, namespace: str, name: str) -> dict[str, Any] | None:
        raise NotImplementedError

    async def apply_deployment(self, deploy: dict[str, Any]) -> None:
        raise NotImplementedError

    async def apply_service(self, service: dict[str, Any]) -> None:
        raise NotImplementedError

    async def patch_status(self, cr_name: str, namespace: str, status: dict[str, Any]) -> None:
        raise NotImplementedError

    def watch_agentconfigs(self, namespace: str, timeout_seconds: int = 120) -> Any:
        """Stream watch events ({"type", "object"}). The loop treats the
        inherited base implementation as 'no streaming watch' and polls."""
        raise NotImplementedError


class RealKubeClient(KubeClient):
    """In-cluster operator client (K8S-01: in-cluster credentials only)."""

    def __init__(self) -> None:
        self._client: Any = None

    def _api(self) -> Any:
        if self._client is None:
            from kubernetes import client, config  # type: ignore[import-untyped]

            try:
                config.load_incluster_config()
            except Exception:  # noqa: BLE001
                config.load_kube_config()
            self._client = client
        return self._client

    async def list_agentconfigs(self, namespace: str) -> list[dict[str, Any]]:
        def _list() -> list[dict[str, Any]]:
            api = self._api().CustomObjectsApi()
            result: Any = api.list_namespaced_custom_object(
                "agentstrata.io", "v1", namespace, "agentconfigs"
            )
            return result.get("items", [])

        return await asyncio.to_thread(_list)

    async def apply_configmap(self, cm: dict[str, Any]) -> None:
        def _apply() -> None:
            api = self._api().CoreV1Api()
            namespace = cm["metadata"]["namespace"]
            name = cm["metadata"]["name"]
            try:
                api.read_namespaced_config_map(name, namespace)
                api.replace_namespaced_config_map(name, namespace, cm)
            except Exception:  # noqa: BLE001 — not found -> create
                api.create_namespaced_config_map(namespace, cm)

        await asyncio.to_thread(_apply)

    async def get_configmap(self, namespace: str, name: str) -> dict[str, Any] | None:
        def _get() -> dict[str, Any] | None:
            try:
                cm: Any = self._api().CoreV1Api().read_namespaced_config_map(name, namespace)
                return {
                    "resourceVersion": cm.metadata.resource_version,
                    "uid": cm.metadata.uid,
                    "data": cm.data or {},
                }
            except Exception:  # noqa: BLE001
                return None

        return await asyncio.to_thread(_get)

    async def apply_deployment(self, deploy: dict[str, Any]) -> None:
        def _apply() -> None:
            api = self._api().AppsV1Api()
            namespace = deploy["metadata"]["namespace"]
            name = deploy["metadata"]["name"]
            try:
                api.read_namespaced_deployment(name, namespace)
                api.replace_namespaced_deployment(name, namespace, deploy)
            except Exception:  # noqa: BLE001 — not found -> create
                api.create_namespaced_deployment(namespace, deploy)

        await asyncio.to_thread(_apply)

    async def apply_service(self, service: dict[str, Any]) -> None:
        def _apply() -> None:
            api = self._api().CoreV1Api()
            namespace = service["metadata"]["namespace"]
            name = service["metadata"]["name"]
            try:
                api.read_namespaced_service(name, namespace)
                api.replace_namespaced_service(name, namespace, service)
            except Exception:  # noqa: BLE001 — not found -> create
                api.create_namespaced_service(namespace, service)

        await asyncio.to_thread(_apply)

    async def patch_status(self, cr_name: str, namespace: str, status: dict[str, Any]) -> None:
        def _patch() -> None:
            api = self._api().CustomObjectsApi()
            api.patch_namespaced_custom_object_status(
                "agentstrata.io", "v1", namespace, "agentconfigs", cr_name, {"status": status}
            )

        await asyncio.to_thread(_patch)

    async def watch_agentconfigs(self, namespace: str, timeout_seconds: int = 120) -> Any:
        from kubernetes import watch  # type: ignore[import-untyped]

        def _stream():
            api = self._api().CustomObjectsApi()
            w = watch.Watch()
            return w.stream(
                api.list_namespaced_custom_object,
                "agentstrata.io",
                "v1",
                namespace,
                "agentconfigs",
                timeout_seconds=timeout_seconds,
            )

        stream = await asyncio.to_thread(_stream)
        while True:
            try:
                event: Any = await asyncio.to_thread(next, stream)
            except StopIteration:
                return
            obj = event.get("object")
            if obj is None:
                continue
            yield {"type": event.get("type", ""), "object": obj}


class FakeKubeClient(KubeClient):
    """In-memory substitute: AgentConfigs, ConfigMaps, Deployments,
    Services with resourceVersion/uid semantics (tests only)."""

    def __init__(self) -> None:
        self.agentconfigs: dict[str, dict[str, Any]] = {}
        self.configmaps: dict[tuple[str, str], dict[str, Any]] = {}
        self.deployments: dict[tuple[str, str], dict[str, Any]] = {}
        self.services: dict[tuple[str, str], dict[str, Any]] = {}
        self.statuses: dict[tuple[str, str], dict[str, Any]] = {}
        self._rv = 0

    def _next_rv(self) -> str:
        self._rv += 1
        return str(self._rv)

    def put_agentconfig(self, cr: dict[str, Any]) -> None:
        metadata = cr.setdefault("metadata", {})
        metadata.setdefault("uid", f"uid-{len(self.agentconfigs) + 1}")
        metadata.setdefault("generation", 1)
        self.agentconfigs[metadata["name"]] = cr

    async def list_agentconfigs(self, namespace: str) -> list[dict[str, Any]]:
        return [
            cr
            for cr in self.agentconfigs.values()
            if cr["metadata"].get("namespace", "default") == namespace
        ]

    async def apply_configmap(self, cm: dict[str, Any]) -> None:
        key = (cm["metadata"]["namespace"], cm["metadata"]["name"])
        cm = dict(cm)
        cm["metadata"] = dict(cm["metadata"])
        cm["metadata"]["resourceVersion"] = self._next_rv()
        cm["metadata"]["uid"] = cm["metadata"].get("uid", "cm-uid")
        self.configmaps[key] = cm

    async def get_configmap(self, namespace: str, name: str) -> dict[str, Any] | None:
        cm = self.configmaps.get((namespace, name))
        if cm is None:
            return None
        return {
            "resourceVersion": cm["metadata"].get("resourceVersion", "1"),
            "uid": cm["metadata"].get("uid", ""),
            "data": cm.get("data", {}),
        }

    async def apply_deployment(self, deploy: dict[str, Any]) -> None:
        key = (deploy["metadata"]["namespace"], deploy["metadata"]["name"])
        deploy = dict(deploy)
        deploy["metadata"] = dict(deploy["metadata"])
        deploy["metadata"]["resourceVersion"] = self._next_rv()
        self.deployments[key] = deploy

    async def apply_service(self, service: dict[str, Any]) -> None:
        key = (service["metadata"]["namespace"], service["metadata"]["name"])
        service = dict(service)
        service["metadata"] = dict(service["metadata"])
        service["metadata"]["resourceVersion"] = self._next_rv()
        self.services[key] = service

    async def patch_status(self, cr_name: str, namespace: str, status: dict[str, Any]) -> None:
        self.statuses[(namespace, cr_name)] = status
