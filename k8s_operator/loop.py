"""AgentConfig operator loop (REQUIREMENTS.md K8S-01).

Entry: reconcile every AgentConfig in the namespace, then stream watch
events with a resync timeout; on watch loss/timeout, re-list and continue.
Status patches are best-effort — the next resync converges.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Any

from .kube import KubeClient
from .reconcile import reconcile

logger = logging.getLogger("agentstrata.operator")

WATCH_TIMEOUT_SECONDS = 120


async def run_operator(
    kube: KubeClient,
    namespace: str,
    resync_seconds: int,
    *,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Reconcile-all, then watch until the stop event (tests) or forever."""
    while True:
        try:
            crs = await kube.list_agentconfigs(namespace)
            for cr in crs:
                await _reconcile_one(kube, namespace, cr)
        except Exception as exc:  # noqa: BLE001 — the loop must survive
            logger.exception("reconcile-all failed: %s", type(exc).__name__)
        if stop_event is not None and stop_event.is_set():
            return
        try:
            await _watch_loop(kube, namespace, resync_seconds, stop_event)
        except Exception as exc:  # noqa: BLE001 — re-list on watch loss
            logger.warning("watch lost (%s); re-listing", type(exc).__name__)


async def _watch_loop(
    kube: KubeClient, namespace: str, resync_seconds: int, stop_event: asyncio.Event | None
) -> None:
    """Stream custom-object watch events; the resync timeout bounds each
    watch so the full re-list runs at least every ``resync_seconds``."""
    if type(kube).watch_agentconfigs is KubeClient.watch_agentconfigs:
        # No streaming watch (fake substitute): the resync poll is the loop.
        if stop_event is not None:
            with suppress(TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=resync_seconds)
        else:
            await asyncio.sleep(resync_seconds)
        return
    async for event in kube.watch_agentconfigs(namespace, timeout_seconds=resync_seconds):
        if stop_event is not None and stop_event.is_set():
            return
        etype = event.get("type", "")
        cr = event.get("object")
        if cr is None:
            continue
        if etype == "DELETED":
            # ownerReferences let the cluster GC handle cleanup.
            continue
        await _reconcile_one(kube, namespace, cr)


async def _reconcile_one(kube: KubeClient, namespace: str, cr: dict[str, Any]) -> None:
    name = (cr.get("metadata") or {}).get("name", "")
    if not name:
        return
    try:
        status = await reconcile(cr, kube)
    except Exception as exc:  # noqa: BLE001 — converge on the next resync
        logger.exception("reconcile %s failed: %s", name, type(exc).__name__)
        return
    try:
        await kube.patch_status(name, namespace, status)
    except Exception:  # noqa: BLE001
        logger.warning("status patch failed for %s", name)
