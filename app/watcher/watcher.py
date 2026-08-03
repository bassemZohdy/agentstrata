"""Kubernetes ConfigMap watcher (REQUIREMENTS.md K8S-01, K8S-02, K8S-05,
K8S-07, K8S-09).

Watch target is a ConfigMap at {namespace}/{name}; key ``agent.yaml`` holds a
UTF-8 tier-8 YAML overlay. Loads in-cluster credentials only. Performs an
initial GET/list, watches from its resourceVersion, handles 410 Gone by
re-listing, re-lists every ``resyncSeconds``, and uses bounded connect/read
timeouts so shutdown cannot hang. Only events for the configured name/UID
affect tier 8; labels/annotations/resource-version are never merged (K8S-09).
Replicas watch independently with no leader election (K8S-07); identical
errors are log-throttled (K8S-05).
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import AsyncIterator
from typing import Any, Protocol

logger = logging.getLogger(__name__)

WATCH_TIMEOUT_SECONDS = 25  # K8S-02: bounded, <= 30s
MAX_CONNECT_BACKOFF = 30.0
THROTTLE_SECONDS = 60.0


class KubeClient(Protocol):
    """The kubernetes surface the watcher uses (real or fake)."""

    async def get_configmap(self, namespace: str, name: str) -> dict[str, Any] | None:
        """Return {resourceVersion, uid, data} or None (404)."""

    def watch_configmap(
        self, namespace: str, name: str, resource_version: str
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield watch events; raises WatchLost on 410/connection loss."""
        raise NotImplementedError


class FakeKubeClient:
    """In-memory substitute for tests: a mutable ConfigMap."""

    def __init__(self, cm: dict[str, Any] | None = None) -> None:
        self.cm = cm
        self.deleted = False
        self.events: list[dict[str, Any]] = []

    async def get_configmap(self, namespace: str, name: str) -> dict[str, Any] | None:
        if self.deleted or self.cm is None:
            return None
        return {
            "resourceVersion": self.cm.get("resourceVersion", "1"),
            "uid": self.cm.get("uid", "uid-1"),
            "data": self.cm.get("data", {}),
        }

    async def watch_configmap(
        self, namespace: str, name: str, resource_version: str
    ) -> AsyncIterator[dict[str, Any]]:
        while self.events:
            yield self.events.pop(0)
        # simulate an ended watch until new events arrive
        yield {"type": "ENDED"}


class ConfigMapWatcher:
    """K8S-02 watch loop; one instance per replica (K8S-07)."""

    def __init__(
        self,
        client: KubeClient,
        namespace: str,
        name: str,
        required: bool,
        resync_seconds: int,
        on_overlay: Any,
    ) -> None:
        self._client = client
        self._namespace = namespace
        self._name = name
        self._required = required
        self._resync_seconds = resync_seconds
        self._on_overlay = on_overlay  # async (overlay: dict | None) -> None
        self._uid: str | None = None
        self._resource_version = ""
        self._last_error_log = 0.0
        self._running = False
        self.health: dict[str, Any] = {
            "connected": False,
            "last_sync": None,
            "last_error": None,
            "applied_overlay": False,
        }

    async def run(self) -> None:
        self._running = True
        backoff = 1.0
        while self._running:
            try:
                cm = await self._client.get_configmap(self._namespace, self._name)
                if cm is None:
                    # K8S-05: initial 404 / deletion is nonfatal.
                    await self._throttled("configmap missing (404/deleted)")
                    self.health["connected"] = False
                    self.health["last_error"] = "configmap missing"
                    await self._on_overlay(None)  # REL-05 fallback to tiers 1-7
                    backoff = min(backoff * 2, MAX_CONNECT_BACKOFF) + random.uniform(0, 0.25)
                    await asyncio.sleep(backoff)
                    continue

                self._uid = cm["uid"]
                self._resource_version = cm["resourceVersion"]
                self.health["connected"] = True
                self.health["last_sync"] = time.time()
                self.health["last_error"] = None
                backoff = 1.0

                overlay = _extract_overlay(cm)
                if overlay is not None:
                    await self._on_overlay(overlay)
                    self.health["applied_overlay"] = True

                await self._watch_loop()
                # re-list every resyncSeconds
                await asyncio.sleep(self._resync_seconds)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                await self._throttled(f"watch error: {exc}")
                self.health["connected"] = False
                self.health["last_error"] = str(exc)[:120]
                backoff = min(backoff * 2, MAX_CONNECT_BACKOFF) + random.uniform(0, 0.25)
                await asyncio.sleep(backoff)

    async def stop(self) -> None:
        self._running = False

    async def _watch_loop(self) -> None:
        """Watch from the current resourceVersion; re-list on 410. The
        client applies the bounded timeout (K8S-02); 410/connection loss is
        surfaced as WatchLost so the caller re-lists."""
        try:
            async for event in self._client.watch_configmap(
                self._namespace, self._name, self._resource_version
            ):
                if not self._running:
                    return
                etype = event.get("type", "")
                if etype == "DELETED":
                    await self._on_overlay(None)
                    self.health["applied_overlay"] = False
                    return
                if etype in ("MODIFIED", "ADDED"):
                    cm = await self._client.get_configmap(self._namespace, self._name)
                    if cm is None:
                        continue
                    if self._uid and cm.get("uid") != self._uid:
                        continue  # K8S-02: only the configured object matters
                    self._resource_version = cm["resourceVersion"]
                    overlay = _extract_overlay(cm)
                    if overlay is not None:
                        await self._on_overlay(overlay)
        except WatchLost:
            return  # caller re-lists (410 handling)

    async def _throttled(self, message: str) -> None:
        now = time.monotonic()
        if now - self._last_error_log >= THROTTLE_SECONDS:
            logger.warning("watcher: %s", message)
            self._last_error_log = now


class WatchLost(Exception):
    """410 Gone / watch connection loss — the caller must re-list."""


def _extract_overlay(cm: dict[str, Any]) -> dict[str, Any] | None:
    """K8S-09: read only data.agent.yaml; never merge labels/annotations/
    managed fields/resource-version."""
    data = cm.get("data") or {}
    raw = data.get("agent.yaml")
    if raw is None:
        return None
    from ..config import parse

    try:
        return parse.parse_yaml_bytes(
            raw.encode("utf-8") if isinstance(raw, str) else raw, "k8s-overlay"
        )
    except parse.SourceError as exc:
        logger.warning("invalid overlay: %s", exc)
        return None


class RealKubeClient:
    """In-cluster kubernetes client (K8S-02: in-cluster credentials only)."""

    def __init__(self) -> None:
        self._core = None

    def _api(self):
        if self._core is None:
            from kubernetes import client, config  # type: ignore[import-untyped]

            try:
                config.load_incluster_config()
            except Exception:  # noqa: BLE001
                config.load_kube_config()
            self._core = client.CoreV1Api()
        return self._core

    async def get_configmap(self, namespace: str, name: str) -> dict[str, Any] | None:
        def _get() -> dict[str, Any] | None:
            try:
                cm: Any = self._api().read_namespaced_config_map(name, namespace)
                return {
                    "resourceVersion": cm.metadata.resource_version,
                    "uid": cm.metadata.uid,
                    "data": cm.data or {},
                }
            except Exception:  # noqa: BLE001
                return None

        return await asyncio.to_thread(_get)

    async def watch_configmap(self, namespace: str, name: str, resource_version: str):
        from kubernetes import watch

        w = watch.Watch()

        def _stream():
            stream = w.stream(
                self._api().list_namespaced_config_map,
                namespace,
                field_selector=f"metadata.name={name}",
                resource_version=resource_version,
                timeout_seconds=WATCH_TIMEOUT_SECONDS,
            )
            yield from iter(stream)

        stream_iter = await asyncio.to_thread(_stream)
        try:
            while True:
                event: Any = await asyncio.to_thread(next, stream_iter)
                obj: Any = event.get("object")
                if obj is None:
                    continue
                etype = event.get("type", "")
                if etype == "DELETED":
                    yield {"type": "DELETED"}
                    return
                yield {
                    "type": etype,
                    "resourceVersion": getattr(obj.metadata, "resource_version", ""),
                    "uid": getattr(obj.metadata, "uid", ""),
                    "data": obj.data or {},
                }
        except StopIteration:
            raise WatchLost() from None
