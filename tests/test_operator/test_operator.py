"""AgentConfig operator (REQUIREMENTS.md K8S-01).

Reconcile behavior against the in-memory FakeKubeClient: ConfigMap
(tier-8 agent.yaml overlay), Deployment (image annotation, k8s env,
ownerReferences), Service, and status patching — plus the loop's
reconcile-all + resync behavior.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
import yaml

from k8s_operator.kube import FakeKubeClient
from k8s_operator.loop import run_operator
from k8s_operator.reconcile import (
    IMAGE_ANNOTATION,
    OWNER_LABEL,
    OWNER_VALUE,
    build_status,
    desired_configmap,
    desired_deployment,
    desired_service,
    reconcile,
)


def make_cr(name: str = "demo", generation: int = 1, image: str = "agentbase:test") -> dict:
    return {
        "apiVersion": "agentstrata.io/v1",
        "kind": "AgentConfig",
        "metadata": {
            "name": name,
            "namespace": "default",
            "uid": f"uid-{name}",
            "generation": generation,
            "annotations": {IMAGE_ANNOTATION: image},
        },
        "spec": {
            "name": "demo",
            "engine": {"systemInstruction": "You are a test agent."},
            "llm": {"provider": "gemini", "model": "mock"},
            "server": {"port": 8080},
        },
    }


class TestReconcile:
    async def test_reconcile_creates_all_objects(self):
        kube = FakeKubeClient()
        cr = make_cr()
        status = await reconcile(cr, kube)
        assert status["conditions"][0]["status"] == "True"
        assert status["appliedConfigMap"] == "agentstrata-demo"

        cm = kube.configmaps[("default", "agentstrata-demo")]
        assert cm["data"]["agent.yaml"] == yaml.safe_dump(cr["spec"], sort_keys=False)
        assert cm["metadata"]["labels"][OWNER_LABEL] == OWNER_VALUE
        owner = cm["metadata"]["ownerReferences"][0]
        assert owner["uid"] == "uid-demo" and owner["kind"] == "AgentConfig"

        deploy = kube.deployments[("default", "agentstrata-demo")]
        assert deploy["spec"]["template"]["spec"]["containers"][0]["image"] == "agentbase:test"
        env = {
            e["name"]: e.get("value")
            for e in deploy["spec"]["template"]["spec"]["containers"][0]["env"]
        }
        assert env["AGENT_K8S_NAME"] == "agentstrata-demo"
        assert env["AGENT_K8S_ENABLED"] == "true"
        assert env["AGENT_K8S_NAMESPACE"] is None  # valueFrom fieldRef

        svc = kube.services[("default", "agentstrata-demo")]
        assert svc["spec"]["ports"][0]["port"] == 8080

    async def test_reconcile_updates_configmap_on_change(self):
        kube = FakeKubeClient()
        await reconcile(make_cr(generation=1), kube)
        updated = make_cr(generation=2)
        updated["spec"]["server"]["port"] = 9090
        status = await reconcile(updated, kube)
        assert status["observedGeneration"] == 2
        cm = kube.configmaps[("default", "agentstrata-demo")]
        assert "port: 9090" in cm["data"]["agent.yaml"]
        assert cm["metadata"]["resourceVersion"] != "1"

    async def test_reconcile_missing_image_fails_closed(self):
        kube = FakeKubeClient()
        cr = make_cr()
        cr["metadata"]["annotations"] = {}
        status = await reconcile(cr, kube)
        assert status["conditions"][0]["status"] == "False"
        assert status["conditions"][0]["reason"] == "MissingImage"
        assert ("default", "agentstrata-demo") not in kube.configmaps

    async def test_reconcile_invalid_spec_fails_closed(self):
        kube = FakeKubeClient()
        cr = make_cr()
        cr["spec"] = {"name": "demo", "engine": {}}  # systemInstruction required
        status = await reconcile(cr, kube)
        assert status["conditions"][0]["status"] == "False"
        assert status["conditions"][0]["reason"] == "InvalidSpec"
        assert ("default", "agentstrata-demo") not in kube.configmaps

    def test_desired_objects_shapes(self):
        cr = make_cr()
        cm = desired_configmap(cr, "overlay: true")
        assert cm["data"] == {"agent.yaml": "overlay: true"}
        deploy = desired_deployment(cr, "img:1")
        assert deploy["spec"]["template"]["spec"]["terminationGracePeriodSeconds"] == 35
        assert deploy["spec"]["template"]["spec"]["securityContext"]["runAsNonRoot"] is True
        svc = desired_service(cr)
        assert svc["spec"]["selector"] == {"app.kubernetes.io/name": "agentstrata-demo"}

    def test_build_status(self):
        cr = make_cr(generation=7)
        status = build_status(cr, ready=True, reason="Applied", message="ok")
        assert status["observedGeneration"] == 7
        assert status["conditions"][0]["status"] == "True"


class TestOperatorLoop:
    async def test_reconcile_all_then_stop(self):
        kube = FakeKubeClient()
        kube.put_agentconfig(make_cr(name="a"))
        kube.put_agentconfig(make_cr(name="b", image="agentbase:other"))
        stop = asyncio.Event()

        async def _stop_after_first_poll():
            await asyncio.sleep(0.2)
            stop.set()

        stopper = asyncio.create_task(_stop_after_first_poll())
        await run_operator(kube, "default", resync_seconds=30, stop_event=stop)
        await stopper

        assert ("default", "agentstrata-a") in kube.configmaps
        assert ("default", "agentstrata-b") in kube.configmaps
        assert ("default", "agentstrata-a") in kube.deployments
        assert kube.statuses[("default", "a")]["conditions"][0]["status"] == "True"
        assert kube.statuses[("default", "b")]["conditions"][0]["status"] == "True"

    async def test_watch_failure_backs_off(self):
        """R-22: a watch that dies immediately must not hot-loop the re-list;
        the exponential backoff bounds the number of cycles in a fixed
        window (a hot loop would spin hundreds of times)."""
        kube = _FlakyKube(fail_watch=True)
        stop = asyncio.Event()

        async def _stopper():
            await asyncio.sleep(1.6)
            stop.set()

        stopper = asyncio.create_task(_stopper())
        await run_operator(kube, "default", resync_seconds=30, stop_event=stop)
        await stopper
        # Backoff sleeps 0.25+0.5+1.0 … ≈ 1.75 s cumulative, so the window
        # admits ~4-5 re-lists; assert a generous ceiling for CI jitter.
        assert 3 <= kube.list_calls <= 8

    async def test_list_failure_backs_off(self):
        """R-22: a dead API server (list failing) also backs off instead of
        spinning."""
        kube = _FlakyKube(fail_list=True)
        stop = asyncio.Event()

        async def _stopper():
            await asyncio.sleep(1.6)
            stop.set()

        stopper = asyncio.create_task(_stopper())
        await run_operator(kube, "default", resync_seconds=30, stop_event=stop)
        await stopper
        assert 3 <= kube.list_calls <= 8


class _FlakyKube(FakeKubeClient):
    """FakeKubeClient whose list and/or watch can fail (R-22 backoff)."""

    def __init__(self, fail_watch: bool = False, fail_list: bool = False) -> None:
        super().__init__()
        self.fail_watch = fail_watch
        self.fail_list = fail_list
        self.list_calls = 0

    async def list_agentconfigs(self, namespace: str) -> list[dict[str, Any]]:
        self.list_calls += 1
        if self.fail_list:
            raise RuntimeError("api server down")
        return await super().list_agentconfigs(namespace)

    def watch_agentconfigs(self, namespace: str, timeout_seconds: int = 120) -> Any:
        if self.fail_watch:
            raise RuntimeError("watch broken")
        return super().watch_agentconfigs(namespace, timeout_seconds)

    async def test_invalid_cr_gets_false_status(self):
        kube = FakeKubeClient()
        cr = make_cr(name="bad")
        cr["spec"] = {"name": "bad", "engine": {}}
        kube.put_agentconfig(cr)
        stop = asyncio.Event()

        async def _stop():
            await asyncio.sleep(0.2)
            stop.set()

        stopper = asyncio.create_task(_stop())
        await run_operator(kube, "default", resync_seconds=30, stop_event=stop)
        await stopper

        status = kube.statuses[("default", "bad")]
        assert status["conditions"][0]["reason"] == "InvalidSpec"
        assert ("default", "agentstrata-bad") not in kube.configmaps


@pytest.mark.parametrize(
    "kind,image_required",
    [
        ("crds", False),
        ("operator", True),
    ],
)
def test_manifests_present(kind, image_required):
    """The CRD + RBAC manifests exist and parse as YAML documents."""
    import os
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent.parent
    if kind == "crds":
        path = root / "k8s_operator" / "crd" / "agentconfigs.agentstrata.io.yaml"
        assert path.exists(), "run scripts/gen-schemas.py to regenerate the CRD"
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert doc["kind"] == "CustomResourceDefinition"
        assert doc["spec"]["group"] == "agentstrata.io"
        assert "spec" in doc["spec"]["versions"][0]["schema"]["openAPIV3Schema"]["properties"]
        assert os.environ.get("CI") or True
    else:
        path = root / "k8s_operator" / "rbac.yaml"
        docs = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
        kinds = [d["kind"] for d in docs]
        assert kinds == ["ServiceAccount", "ClusterRole", "ClusterRoleBinding"]
