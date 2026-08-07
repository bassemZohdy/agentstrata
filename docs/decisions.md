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

## Per-request override transport — R-33 (API-12)

- **Problem:** `temperature` / `max_tokens` overrides were validated and
  gated but silently discarded — the R-20 removal of the
  `RunConfig(temperature=…)` kwargs fixed a real latent bug
  (`ValidationError` on the current google-adk degraded every overridden
  run to `provider_error`) but left API-12 half-honoured: a caller
  tuning temperature got a clean 200 and silently different sampling.
- **Decision:** APPLY the overrides to the provider call (option a of
  the R-33 record — not a spec amendment).  Transport: the values ride
  in `RunConfig.labels` (ADK merges run-config labels into the per-step
  `LlmRequest.config.labels` in the basic flow), and the existing
  `RetryableLlm` wrapper — already in the call path — applies them to
  `GenerateContentConfig.temperature` / `.max_output_tokens` and strips
  the synthetic labels before the provider call.  Labels travel with
  the invocation, so concurrent runs never share override state (the
  R-03 singleton lesson).  The R-20 note's premise ("not expressible
  until google-adk exposes the seam") was too pessimistic — the seam
  exists and is public.
- **Cap enforcement:** values must satisfy the base schema + the
  configured override maximum; above-cap or non-finite values return
  400 `override_not_allowed` (API-12), never silently clamped — now
  that overrides actually reach the provider, an unchecked value would
  be forwarded as-is.
- **Status:** resolved (2026-08-06, R-33).  Tests:
  `tests/test_engine/test_overrides.py` + `TestOverrideApplication` in
  test_api.py (seam applies + strips labels; caps rejected end to end).

## E1 — env-first configuration decisions (2026-08-07)

### E1-2: schema defaults stand alone; tier 1 stays the operational default

- **Decision:** the required schema leaves stay required — `name`,
  `engine.systemInstruction`, `llm.model` (all env-bindable) — and
  `AGENT_BUNDLED_DIR` pointing at an empty directory boots when they are
  supplied via env (CFG-16).  `name` was NOT given a schema default:
  SCH-02 treats changing defaults as a schema-major change, and the
  bundled tier-1 `config/agent.yaml` already supplies the release-tested
  values (BASE-01).  Everything else has schema defaults, so the
  minimum viable env set is three leaves + the provider credential.
- **Status:** resolved (E1-2).

### E1-3: collections stay JSON-only, with an explicit signpost

- **Decision:** option (c) — `AGENT_APPLICATION_JSON` remains the only
  per-item path for collections (`tools.mcpServers`, `agents`,
  `costs.models`).  Indexed-convention (a) and per-entry-DSL (b) would
  each become a frozen binding surface maintained forever, with nested
  list-of-model fields (e.g. `mcpServers[].args`) making (a) explode
  combinatorially and (b) adding a mini-language to parse and validate.
  JSON is the config's native form and already reachable from env
  alone.  CFG-08 now signposts the dead end: a list-index-shaped
  `AGENT_*` variable (suffix contains `_<digits>_` / ends `_<digits>`)
  warns that collection items are not env-bindable and names
  `AGENT_APPLICATION_JSON`.
- **Status:** resolved (E1-3); recorded in REQUIREMENTS 2.6 (CFG-08).

### E1-4: short-alias table (frozen surface)

- **Decision:** the closed alias table is exactly four entries —
  `AGENT_MODEL` → `llm.model`, `AGENT_INSTRUCTION` →
  `engine.systemInstruction`, `AGENT_API_KEY` → `llm.apiKeyEnv`,
  `AGENT_PROVIDER` → `llm.provider`.  Canonical names always win over
  aliases for the same target path (regardless of OS env order); aliases
  participate in ambiguity detection.  This set is FROZEN once
  published — additions are possible, removals are not (CFG-07 item 4).
- **Status:** resolved (E1-4).

### E1-5: opt-in credential-variable inference

- **Decision:** new `llm.autoApiKeyEnv: bool = false` (opt-in per
  LLM-04).  When enabled and no `apiKeyEnv`/`apiKeyFile` is set, the
  variable name is inferred from a deterministic table (`gemini` →
  `GEMINI_API_KEY`, `openai` → `OPENAI_API_KEY`, `anthropic` →
  `ANTHROPIC_API_KEY`; `ollama`/`litellm`/vertex-ADC infer nothing).
  An inferred-but-absent variable fails boot validation naming the
  variable (SEC-03 fail-closed) — never a silent keyless start.  The
  OBS-03 startup line names the variable, never the value.
- **Status:** resolved (E1-5).
