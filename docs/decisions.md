# Decision log

Resolved engineering and product decisions, recorded for the record
(requirement IDs trace back to [REQUIREMENTS.md](../REQUIREMENTS.md)).
The traceability matrix (`docs/traceability.md`) maps decisions that
satisfy a requirement ID to this file.

## NFR-06 — OpenAI SDK compatibility range

- **Decision:** the `openai` Python SDK range is `>=1.0,<3` (locked
  2.52.0); the exposed surface is a strict subset.
- **Status:** resolved.
- **Verification:** `tests/test_protocol/test_openai_sdk.py`.

## CNT-12 — vulnerability severity policy

- **Decision:** a CRITICAL or HIGH CVE with no available fix blocks a
  release; MEDIUM/LOW findings are tracked, non-blocking.
- **Status:** resolved.
- **Enforcement:** the release gate is documented in
  `docs/release.md` (Vulnerability gate); see also the M8 scan gate.

## MCP-08 — stdio pre-parse byte cap

- **Decision:** the stdio pre-parse byte cap is deferred until
  `google-adk` supports `mcp` 2.x; the HTTP/SSE cap is enforced now.
- **Status:** deferred (external dependency).
- **Note:** consistent with the google.adk MCPTool deprecation decision
  below — both wait on upstream google-adk releases.

## mcp SDK version range

- **Decision:** pinned `mcp>=1.24,<2` (locked 1.29.0).
- **Status:** resolved.

## ACC-01 — storage proof deviation

- **Decision:** unit tests use the memory backend as a substitute;
  real Redis/Postgres proofs are deferred to the release acceptance
  run.
- **Status:** resolved (deviation recorded).

## Product name

- **Decision:** "Agentbase"/"AgentStrata" stays for open-source,
  non-commercial use; `agent-strata` is the registry-clear fallback if
  the project ever turns commercial.
- **Status:** resolved.

## google.adk MCPTool deprecation

- **Decision:** no migration applies yet — the `DeprecationWarning:
  MCPTool class is deprecated, use McpTool instead` fires inside
  google-adk's own `mcp_toolset.py` (verified in 2.6.1 and 2.6.2, which
  still construct the deprecated class); this codebase already uses the
  modern `McpToolset` API (zero `MCPTool` references).
- **Status:** deferred (external dependency).
- **Note:** a google-adk bump is a requirements-impacting change
  (STACK-01) and must go through the same review as a REQUIREMENTS.md
  change.

## CRD API group (`agentstrata.io`) vs product name "Agentbase"

- **Decision:** keep the CRD API group `agentstrata.io` pre-1.0 (K8S-11
  pins it in REQUIREMENTS.md).  It matches the repository identity and
  the documented registry-clear fallback (`agent-strata`); the group is
  invisible to runtime users (only cluster operators apply CRs), and a
  pre-1.0 rename is a one-line change with no published resources to
  migrate.
- **Status:** resolved (revisit only if the product name definitively
  changes — an API-group rename post-1.0 would be a breaking migration
  and a versioned spec revision).

## Usage shape across surfaces (chat / ACP / WebSocket) — R-14

- **Decision:** all three run surfaces emit the SAME normalized usage
  shape — `prompt_tokens` / `completion_tokens` / `total_tokens` plus
  `costUsd` (camelCase) when `costs.enabled` computed a cost
  (COST-01).  `_normalize_usage()` in `app/protocol/routes/chat.py` is
  the single implementation used by chat, ACP (annex A-4 non-streaming),
  and the WS `run.done` payload.
- **ACP streaming usage chunk:** confirmed against the annex — A-4 says
  the streaming vocabulary includes an "optional usage chunk" and the
  ACP request contract has no `stream_options` field, so the chunk stays
  omitted on ACP streams (annex-consistent; adding the field would be a
  versioned annex change).
- **Status:** resolved (2026-08-06, R-14).
