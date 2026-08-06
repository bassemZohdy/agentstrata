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
