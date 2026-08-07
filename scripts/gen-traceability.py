#!/usr/bin/env python3
"""Generate the TRC-01 traceability matrix (REQUIREMENTS.md TRC-01).

Maps every requirement ID in REQUIREMENTS.md to the module(s)/tests that
verify it. Human-readable markdown is written to docs/traceability.md; the
check fails when any requirement ID has no mapping and no explicit
deferred note. Run: .venv/bin/python scripts/gen-traceability.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ID -> verification locations (module::symbol or test file / note).
# Fill as milestones land; the script validates completeness.
MAPPINGS: dict[str, str] = {
    # Milestone 0
    "STACK-01": "requirements.txt/lock; scripts/compile-lock.sh, verify-lock.sh; PLAN.md rule",
    "STACK-02": "CHANGELOG.md M0 spike; scripts/spike_*.py; REQUIREMENTS.md v2.5 note",
    "CNT-01": "Dockerfile (digest-pinned base, --require-hashes venv)",
    "CNT-04": "Dockerfile ENTRYPOINT; app/main.py",
    "CNT-05": "Dockerfile VOLUME/EXPOSE",
    "CNT-06": "Dockerfile ENV",
    "CNT-08": "app/main.py uvicorn.run(workers=1, reload=False)",
    "CNT-10": "app/healthcheck.py; Dockerfile HEALTHCHECK",
    "CNT-11": "Dockerfile USER 10001:0 + read-only rootfs notes; docs/deployment.md",
    "CNT-02": "Dockerfile (multi-arch buildx); docs/release.md (buildx QEMU manifest)",
    "CNT-03": "Dockerfile USER 10001:0",
    "CNT-07": "app/lifecycle.py (ShutdownManager/ManagedServer); "
    "docs/deployment.md graceful shutdown",
    "CNT-09": "docker-compose.yaml",
    "CNT-12": "docs/decisions.md (severity policy); docs/release.md (vulnerability gate)",
    "CNT-13": ".dockerignore; Dockerfile secrets hygiene",
    "DEL-01": "app/ layout; docs/deployment.md; Dockerfile",
    "DEL-02": "scripts/gen-schemas.py; schemas/; CI zero-diff",
    # Milestone 1
    "SCH-01": "app/config/models.py",
    "SCH-02": "scripts/gen-schemas.py; schemas/agent.schema.json; tests/test_config/test_schema.py",
    "SCH-03": "app/config/models.py (Engine/Llm); tests/test_config/test_schema.py",
    "SCH-04": "app/config/models.py (McpServer/SecretHeaderRef)",
    "SCH-05": "app/config/models.py (Storage)",
    "SCH-06": "app/config/models.py (Server)",
    "SCH-07": "app/config/models.py (K8s)",
    "SCH-08": "app/config/models.py (Observability)",
    "SCH-09": "app/config/models.py (phase stubs)",
    "BASE-01": "config/agent.yaml; tests/test_config/test_schema.py::TestBundledBase",
    "CFG-01": "app/config/resolver.py",
    "CFG-02": "app/config/resolver.py::resolve_config_dir",
    "CFG-03": "app/config/resolver.py::resolve_profile",
    "CFG-03a": "app/config/parse.py",
    "CFG-03b": "app/config/resolver.py::_first_existing; tests/test_config/test_resolver.py",
    "CFG-04": "app/config/resolver.py::_apply_tier",
    "CFG-05": "app/config/resolver.py::_apply_tier (list replacement); tests",
    "CFG-06": "app/config/resolver.py::_apply_tier (null reset); tests",
    "CFG-07": "app/config/resolver.py::bind_env; tests/test_config/test_resolver.py",
    "CFG-08": "app/config/resolver.py::bind_env (warnings)",
    "CFG-09": "app/config/resolver.py::parse_scalar",
    "CFG-10": "app/config/resolver.py::parse_cli_values; app/config/cli.py",
    "CFG-10a": "app/config/cli.py::run (--validate); tests/test_config/test_cli.py",
    "CFG-10b": "app/config/cli.py::run (--print-env); app/config/env_catalog.py",
    "CFG-11": "app/config/dump.py; tests/test_config/test_dump.py",
    "CFG-11a": "app/config/cli.py (--version/--help)",
    "CFG-12": "app/config/validate.py::validate_resolution",
    "CFG-13": "app/config/validate.py::_shape_walk",
    "CFG-14": "app/config/validate.py::_cross_field",
    "CFG-15": "app/main.py::run (boot order)",
    "CFG-16": "resolver.py::load_file_tiers (tier-1 skip); test_env_epic.py (env-only boot)",
    "CFG-17": "app/config/env_catalog.py; cli.py::run (--print-env); scripts/gen-env-reference.py",
    "CFG-18": "app/config/resolver.py::bind_env (env:<VAR>); cli.py::run (unmatched summary)",
    "CAP-01": "app/config/validate.py::_capability; app/config/capabilities.py",
    "CAP-02": "app/config/capabilities.py::capability_status; app/protocol/routes/health.py",
    "MODE-01": "app/config/mode.py",
    "MODE-02": "app/main.py::run",
    "MODE-03": "app/config/mode.py (fail-closed)",
    "MODE-04": "app/config/mode.py (standalone no watch)",
    "NFR-05": "tests/test_config/test_dump.py::TestNfr05",
    # Milestone 2
    "SES-01": "app/storage/model.py",
    "SES-02": "app/storage/* (create semantics); tests/test_storage/test_contract.py",
    "SES-03": "app/protocol/auth.py (principals); app/storage/*",
    "SES-04": "app/storage/contract.py (unavailable); app/storage/file_backend.py (probe)",
    "SES-05": "app/storage/* (fencing); tests/test_storage/test_contract.py::TestFencing",
    "SES-06": "app/storage/* (sweep/ttl); app/protocol/app.py (lifespan sweep task); "
    "tests/test_storage/test_contract.py::TestSweep",
    "SES-07": "app/storage/* (capacity); tests",
    "SES-08": "app/storage/* (delete cascade + close)",
    "SES-09": "app/storage/adk_adapter.py; tests/test_storage/test_adk_adapter.py",
    # Milestone 3
    "LLM-01": "app/engine/connectors.py::build_llm",
    "LLM-01a": "app/config/models.py::Provider (enum stability; REQUIREMENTS 2.7)",
    "LLM-02": "app/engine/connectors.py (CredentialHealth/SecretResolver)",
    "LLM-03": "app/engine/connectors.py::RetryableLlm",
    "LLM-04": "models.py INFERRED_API_KEY_ENV; validate.py fail-closed",
    "LLM-05": "app/engine/model_catalog.py; validate.py (tools gate); agent.py (context default)",
    "LLM-06": "models.py RagEmbeddingProvider; rag.py::build_embedding",
    "ENG-01": "app/engine/agent.py",
    "ENG-02": "app/engine/runner.py::AgentRunner; app/engine/events.py",
    "ENG-03": "app/engine/runner.py::_admit (order)",
    "ENG-04": "app/engine/context.py",
    "ENG-05": "app/engine/events.py::RunStateMachine",
    "ENG-06": "app/engine/runner.py::_commit_success/_commit_failure; truncate_session_events",
    "ENG-07": "app/engine/limits.py::RunLimiter; tests/test_engine/test_limits.py",
    "ENG-08": "app/engine/limits.py (TokenAccount)",
    "ENG-09": "app/engine/tools.py::ToolLedger",
    "ENG-10": "app/engine/events.py::sanitize_error; app/protocol/errors.py",
    # Milestone 4
    "MCP-01": "app/engine/mcp/manager.py",
    "MCP-02": "app/engine/mcp/manager.py::readiness",
    "MCP-03": "app/engine/mcp/filtering.py",
    "MCP-04": "app/engine/mcp/filtering.py (results)",
    "MCP-05": "app/engine/mcp/manager.py (lifecycle)",
    "MCP-06": "app/engine/mcp/stdio_sandbox.py",
    "MCP-07": "app/engine/tools.py (no auto-retry)",
    "MCP-08": "app/engine/mcp/bounds.py (HTTP/SSE); REQUIREMENTS.md v2.5 (stdio deferred)",
    # Milestone 5
    "API-00": "app/protocol/app.py (middleware)",
    "API-01": "app/protocol/routes/health.py (/healthz)",
    "API-02": "app/protocol/routes/health.py (/readyz)",
    "API-03": "app/protocol/routes/health.py (/health)",
    "API-04": "app/protocol/routes/health.py (/config)",
    "API-05": "app/protocol/routes/chat.py (validation)",
    "API-06": "app/protocol/routes/chat.py (_extract_user_message)",
    "API-06a": "app/protocol/routes/chat.py (idempotency)",
    "API-07": "app/protocol/routes/chat.py (_non_streaming_body)",
    "API-08": "app/protocol/routes/chat.py (_stream)",
    "API-08a": "app/protocol/routes/chat.py (SSE error events + bounded "
    "queue/backpressure); tests/test_protocol/test_streaming.py",
    "API-09": "app/protocol/routes/sessions.py",
    "API-12": "app/protocol/routes/chat.py (overrides gating)",
    "API-15": "app/protocol/errors.py",
    "API-17": "app/protocol/routes/sessions.py::register_models",
    "API-18": "scripts/gen-schemas.py; schemas/openapi.json",
    "API-19": "app/protocol/routes/chat.py (snake_case surface)",
    "API-20": "app/protocol/http_limits.py::BoundedH11Protocol; app/main.py",
    "SEC-01": "app/protocol/auth.py; app/main.py (bind warning)",
    "SEC-02": "app/security/redact.py",
    "SEC-03": "app/main.py (fail-closed boot)",
    "SEC-04": "app/engine/connectors.py::SecretResolver; app/protocol/auth.py",
    "SEC-05": "app/security/audit.py::validate_egress_targets",
    "SEC-06": "app/protocol/app.py (CORS)",
    "SEC-08": "app/protocol/auth.py::_JwtAuth",
    "SEC-09": "app/security/audit.py::parse_forwarded_for",
    "SEC-10": "app/security/audit.py::audit",
    "SEC-11": "app/security/audit.py (hardening headers + guards)",
    "NFR-06": "tests/test_protocol/test_openai_sdk.py; docs/decisions.md",
    # Milestone 6
    "K8S-01": "app/watcher/watcher.py",
    "K8S-02": "app/watcher/watcher.py (watch loop)",
    "K8S-03": "app/watcher/reload.py::_resolve_with_overlay; schemas/agent-overlay.schema.json",
    "K8S-04": "app/watcher/reload.py::apply_tier8",
    "K8S-05": "app/watcher/watcher.py (_throttled)",
    "K8S-07": "app/watcher/watcher.py (per-replica)",
    "K8S-08": "manifests/rbac.yaml, deployment.yaml",
    "K8S-09": "app/watcher/watcher.py::_extract_overlay",
    "REL-01": "app/watcher/reload.py::apply_tier8",
    "REL-02": "app/watcher/reload.py::classify_change",
    "REL-03": "app/watcher/reload.py (transactional swap)",
    "REL-04": "app/watcher/reload.py (generation/hash); app/protocol/routes/health.py",
    "REL-05": "app/watcher/reload.py + watcher (fallback)",
    "REL-06": "app/watcher/reload.py::_audit",
    "NFR-08": "scripts/image-nfr.py (nfr08_reload: zero-downtime reload, docs/nfr-report.json)",
    "SEC-07": "app/engine + app/engine/mcp (untrusted input bounded/redacted); docs/deployment.md",
    "TRUST-01": "app/config/parse.py (trusted source handling); app/protocol/auth.py; MCP bounds",
    "TRUST-02": "app/storage/* (isolation); tests/test_storage/test_contract.py",
    "TRUST-03": "connectors.py; watcher.py; otel.py (nonfatal deps)",
    "PROD-01": "REQUIREMENTS.md §1 (product boundary)",
    "PHASE-01": "REQUIREMENTS.md §1.3; app/config/capabilities.py (CAP-02)",
    "DOC-01": "REQUIREMENTS.md (document contract)",
    "GATE-01": "deleted in v2.2 scope pass (referenced only in revision history)",
    "API-13": "app/config/models.py (engine.streaming); app/protocol/routes/chat.py",
    "API-14": "app/protocol/routes/chat.py (usage reporting)",
    "API-16": "REQUIREMENTS.md §13.1 annex; tests/test_protocol/test_acp.py (A-1..A-6)",
    "HITL-01": "app/config/models.py (ApprovalConfig); tests/test_config/test_validation.py",
    "HITL-02": "app/storage/*_backend.py (approvals); tests/test_storage/test_contract.py",
    "HITL-03": "app/protocol/routes/{chat,approvals}.py; tests/test_protocol/test_approval_api.py",
    "HITL-04": "app/engine/runner.py::resume_approval; tests/test_engine/test_approval_gate.py",
    "HITL-05": "app/engine/runner.py::reconcile_pending; tests/test_engine/test_approval_gate.py",
    "HITL-06": "tests/test_engine/test_approval_gate.py + tests/test_protocol/test_approval_api.py",
    "RAG-01": "app/config/models.py (RagConfig); tests/test_config/test_validation.py",
    "RAG-02": "app/engine/rag.py + runner context injection; tests/test_engine/test_rag.py",
    "RAG-03": "app/protocol/routes/documents.py; tests/test_protocol/test_documents.py",
    "RAG-04": "app/engine/runner.py + health.py; tests/test_engine/test_rag.py",
    "RAG-05": "app/engine/rag_connectors.py + reload.py; tests/test_engine/test_rag.py",
    "RAG-06": "tests/test_engine/test_rag.py + test_documents.py + test_validation.py",
    "ACC-01": "tests/; M8 acceptance run on the built image (storage per recorded deviation)",
    "NFR-00": "Milestone 8 benchmark/chaos run (scripts/benchmark.py)",
    "NFR-01": "Milestone 8 benchmark (startup latency)",
    "NFR-02": "Milestone 8 benchmark (request overhead)",
    "NFR-03": "Milestone 8 benchmark (concurrency)",
    "NFR-04": "Milestone 8 benchmark (idle footprint)",
    "NFR-07": "Milestone 8 chaos (slow/disconnected client)",
    "NFR-09": "Milestone 8 chaos (dependency recovery)",
    "NFR-10": "Milestone 8 (cross-platform portability)",
    "TRC-01": "scripts/gen-traceability.py; docs/traceability.md",
    "TRC-02": "docs/release.md (image digest/commit/lock hash/test results)",
    # Milestone 7
    "MA-01": "app/config/models.py (agents[]); tests/test_config/test_validation.py",
    "MA-02": "app/engine/agent.py (coordinator/sub_agents); tests/test_engine/test_multiagent.py",
    "MA-03": "app/engine/mcp/manager.py (tool_targets); tests/test_engine/test_multiagent.py",
    "MA-04": "app/engine/events.py (AgentTransfer) + chat.py; tests/test_engine/test_multiagent.py",
    "MA-05": "app/watcher/reload.py; tests/test_watcher/test_reload.py + test_multiagent.py",
    "OBS-01": "app/observability/logging.py",
    "OBS-02": "app/observability/logging.py (request id/traceparent)",
    "OBS-03": "app/observability/lifecycle.py",
    "OBS-04": "app/observability/otel.py",
    "OBS-05": "app/observability/metrics.py + otel.py + protocol/routes/metrics.py; "
    "tests/test_protocol/test_metrics.py",
    "OBS-06": "tests/test_observability/test_obs.py (zero-cost subprocess)",
    "WS-01": "app/protocol/routes/websocket.py; tests/test_protocol/test_websocket.py",
    "WS-02": "tests/test_protocol/test_websocket.py",
    "K8S-11": "k8s_operator/reconcile.py + kube.py + loop.py; tests/test_operator/test_operator.py",
    "K8S-12": "tests/test_operator/test_operator.py",
    "COST-01": "app/engine/runner.py (_cost_usd) + config/models.py::Costs + "
    "observability/metrics.py (agentbase_cost_usd_total) + "
    "config/capabilities.py (health `costs`); tests/test_protocol/test_cost.py",
    "COST-02": "tests/test_protocol/test_cost.py + tests/test_config/test_validation.py",
    # P2 ACP acceptance annex (§13.1): implemented in app/protocol/routes/acp.py
    # (gated by server.protocols.acp in app/protocol/app.py), golden fixtures
    # in tests/test_protocol/test_acp.py.
    "A-1": "app/protocol/routes/acp.py (prefix); app/protocol/app.py (protocols.acp gating)",
    "A-2": "app/protocol/routes/acp.py (GET /acp/agents); tests/test_protocol/test_acp.py",
    "A-3": "app/protocol/routes/acp.py (POST /acp/runs request); tests/test_protocol/test_acp.py",
    "A-4": "app/protocol/routes/acp.py (POST /acp/runs response); tests/test_protocol/test_acp.py",
    "A-5": "app/protocol/routes/acp.py (auth/session/idempotency/errors); "
    "tests/test_protocol/test_acp.py",
    "A-6": "tests/test_protocol/test_acp.py (golden fixtures)",
}


def main() -> int:
    text = (ROOT / "REQUIREMENTS.md").read_text(encoding="utf-8")
    # TRC-01: requirement IDs are UPPER-words that may contain digits in the
    # prefix (e.g. K8S-01), followed by -NN with an optional letter suffix
    # (e.g. CFG-11a); the P2 ACP annex uses single-letter IDs A-1..A-6.
    # (The two alternatives keep prose like P5-4/SHA-256/UTF-8 out.)
    ids = sorted(
        set(
            re.findall(
                r"\b(?:[A-Z][A-Z0-9]{1,4}-\d{2}[a-z]?|[A-Z]-\d{1,2}[a-z]?)\b",
                text,
            )
        )
    )
    missing = [i for i in ids if i not in MAPPINGS]
    if missing:
        print("unmapped requirement IDs:", ", ".join(missing), file=sys.stderr)
        return 1
    lines = [
        "# Requirement traceability matrix (TRC-01)",
        "",
        "Auto-generated by `scripts/gen-traceability.py`. Every requirement ID",
        "maps to the module(s)/tests verifying it. IDs marked *pending* are",
        "verified by the Milestone 8 acceptance/chaos run.",
        "",
        "| Requirement | Verification |",
        "| --- | --- |",
    ]
    for i in ids:
        lines.append(f"| {i} | {MAPPINGS[i]} |")
    out = ROOT / "docs" / "traceability.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(ids)} requirements mapped)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
