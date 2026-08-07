# Backlog — remaining work

Status: the 2026-08-05 deep-review backlog (P1–P5, incl. the P5-4 finish
line) is **closed** and recorded in [CHANGELOG.md](CHANGELOG.md). The
2026-08-06 review backlog is **fully closed** — R-01…R-32, the six
review-loop follow-ups, and the six stragglers (R-03/R-12/R-19/R-21/
R-25/R-33) all landed; see CHANGELOG.md ("2026-08-06 review backlog —
closed" and "2026-08-06 review stragglers — closed"). Per-commit
verification narratives are in [docs/review-log.md](docs/review-log.md).
This file tracks only what is **still open**: the two planned-scope
epics (E1, E2) below. Resolved decisions live in
[docs/decisions.md](docs/decisions.md); requirement IDs trace to
[REQUIREMENTS.md](REQUIREMENTS.md); build order and rationale are in
[PLAN.md](PLAN.md).

## Review log

The 2026-08-06/07 monitoring-loop history (per-commit findings and
verification narratives, `9408092` → `6af55be`) lives in
[docs/review-log.md](docs/review-log.md). New passes append there.

---

# Planned scope — minimal configuration + full model coverage
*Restored 2026-08-07 by the review loop: the `# Planned scope` heading,
this preamble, the Baseline section, and the `## E1` heading below were
dropped incidentally by `6af55be` (a straggler-closing commit), which
left the seven open E1 tasks sitting under the "Open 2026-08-06 review
items (all closed)" heading. Text recovered verbatim from `820290d`;
none of that commit's own content was touched.*

Requested 2026-08-06. Two epics. **Both are requirements-impacting**:
they change documented surfaces (CFG-07/CFG-08 env binding, LLM-01
provider set), so each needs a REQUIREMENTS.md amendment and a
`docs/decisions.md` entry *before* implementation, per the project's own
change policy. Neither is a pure dependency bump.

## Intelligence classification — open work

Every open task carries a level (High = architectural / concurrency /
contract design; Medium = localized correctness / security; Low =
mechanical cleanup / docs / CI).

| Level | Tasks |
| --- | --- |
| **High** | **E1-3** collection env-binding (a new frozen binding surface + CFG-07 ambiguity), **E2-2** per-provider credential contracts (SEC-04 secret shapes + redaction), **E2-5** model capability registry (data model + refresh policy), **E2-7** per-sub-agent cost pricing (cost attribution across the agent tree; closes MA-02), **E2-8** fallback chains (interacts with LLM-03 retry + COST-01 attribution) |
| **Medium** | **E1-2** minimum viable env set, **E1-4** short aliases (frozen surface), **E1-5** provider key auto-detection (SEC-03 fail-closed), **E2-1** provider enum expansion (published enum; stability policy), **E2-3** OpenAI-compatible provider, **E2-4** per-provider cross-field validation, **E2-6** default pricing catalog, **E2-9** embedding parity (interlocks R-16) |
| **Low** | **E1-1** env-var catalog + CI gate, **E1-6** binding diagnostics, **E1-7** documentation, **E2-10** verification and documentation |

## Baseline — what already exists (do not rebuild)

Read this first; roughly half of "config via env" is already shipped.

- **Tier 5 env binding works today.** `app/config/resolver.py` derives
  `AGENT_*` variable names from the Pydantic schema
  (`_env_alias`/`_build_binding_index`, `models.py:iter_schema_fields`),
  binds them relaxed/schema-aware, reserves
  `AGENT_PROFILE`/`AGENT_CONFIG_DIR`/`AGENT_APPLICATION_JSON`/`AGENT_BUNDLED_DIR`,
  warns on unmatched variables with a nearest-path hint, and raises
  `AmbiguousEnvError` when one variable matches two schema paths.
- **Zero-file boot already works.** Tier 1 `config/agent.yaml` supplies
  `name`, `engine.systemInstruction`, and a gemini binding, so
  `docker run -e GEMINI_API_KEY=… agentbase` runs with no mounted file.
- **Multi-provider already exists via LiteLLM.** `Provider` =
  `gemini | openai | anthropic | ollama | litellm`;
  `connectors.py:_llm_model_string` prefixes openai/anthropic/ollama and
  passes `litellm` through verbatim, so arbitrary LiteLLM model strings
  are *already* reachable through the `litellm` escape hatch.
- **`llm.extra`** is a CFG-13 passthrough map into the LiteLLM kwargs.

The gaps below are what is genuinely missing.

## E1 — Minimal configuration: environment variables as a first-class surface

Goal: a working agent from environment variables alone, with discoverable
names and sane defaults — no YAML authoring required.

### E1-1 Publish the env-var catalog (currently undocumented)

The variable names are derivable only by reading the resolver. No doc,
README table, or CLI command lists them; `AGENT_LLM_MODEL` appears
nowhere in `docs/` or `README.md`.

- [ ] Add a `--print-env` (or `--dump-env`) CLI action to
      `app/config/cli.py` that emits every bindable path with its
      `AGENT_*` name, type, default, and whether it is secret.
- [ ] Generate `docs/env-reference.md` from the schema via a script in
      `scripts/` (mirroring `scripts/gen-schemas.py`).
- [ ] Add a CI zero-diff gate for the generated reference, exactly like
      the existing "Schema zero-diff (SCH-02, DEL-02)" step.
- [ ] Redact secret-bearing entries per SEC-02 (`is_sensitive_key`).

### E1-2 Guarantee and document the minimum viable env set

- [ ] Audit every schema leaf for a usable default; list the ones with
      no default (today `llm.model` and `name` are the load-bearing ones,
      supplied by tier 1 rather than by field defaults).
- [ ] Decide whether schema defaults should stand alone without tier 1,
      so `AGENT_BUNDLED_DIR` pointing nowhere still boots.
- [ ] Document the true minimum per provider (e.g. gemini: one API key;
      openai: key + model) in README Quick start.
- [ ] Test: boot with **only** env vars and no config file at all.

### E1-3 Collection config is not env-bindable

`iter_schema_fields` marks list-of-model and passthrough keys
non-bindable, so `tools.mcpServers`, `agents`, and `costs.models` cannot
be set by discrete env vars — only wholesale through
`AGENT_APPLICATION_JSON`.

- [ ] Choose one: (a) an indexed convention
      (`AGENT_TOOLS_MCPSERVERS_0_NAME=…`), (b) a compact per-entry DSL
      (`AGENT_MCP_SERVERS="fs=stdio:npx -y @mcp/fs"`), or (c) keep JSON
      as the only path.
- [ ] If (c), make the unmatched-variable warning name
      `AGENT_APPLICATION_JSON` explicitly so the dead end is signposted.
- [ ] Preserve CFG-07 ambiguity detection under whichever scheme wins.
- [ ] Tests: multi-server MCP + multi-agent config from env alone.

### E1-4 Short aliases for the high-traffic knobs

`AGENT_ENGINE_SYSTEM_INSTRUCTION` and `AGENT_LLM_MODEL` are long; a
minimal-config story wants `AGENT_MODEL`, `AGENT_INSTRUCTION`,
`AGENT_API_KEY`, `AGENT_PROVIDER`.

- [ ] Define a small, closed alias table (not a heuristic) mapping short
      names to schema paths.
- [ ] Aliases must lose to the fully-qualified name and must participate
      in `AmbiguousEnvError`.
- [ ] Record the alias set in `docs/decisions.md` — it is a frozen
      surface once published.
- [ ] Tests: alias binds, alias+canonical conflict, alias precedence.

### E1-5 Provider key auto-detection (opt-in)

Today `llm.apiKeyEnv` must name the variable. A minimal setup would infer
`OPENAI_API_KEY`/`ANTHROPIC_API_KEY`/`GEMINI_API_KEY` from the provider.

- [ ] Per-provider default `apiKeyEnv` when unset (deterministic table,
      no scanning of the environment).
- [ ] Keep SEC-03 fail-closed: an inferred-but-absent key still fails
      validation at boot with a clear message.
- [ ] Emit an OBS-03 startup line naming the *variable* used, never the
      value.
- [ ] Tests: inference per provider; explicit config always wins.

### E1-6 Binding diagnostics

- [ ] Extend `--dump-config` provenance (already tracks tier + source) to
      a human-readable "what bound from where" report for env tiers.
- [ ] Promote unmatched-`AGENT_*` warnings to a boot summary line rather
      than scattered warnings.
- [ ] Tests: provenance shows tier 5/6/7 attribution correctly.

### E1-7 Documentation

- [ ] README: a "zero-file agent" quick start next to the existing
      8-tier table.
- [ ] `docs/deployment.md`: env-only Kubernetes/Compose examples.
- [ ] Update `docs/traceability.md` for CFG-07/CFG-08 once the surface
      changes.

## E2 — Full LLM model and integration coverage

Goal: every mainstream provider is first-class and validated, not just
reachable through the `litellm` verbatim escape hatch.

**Sequencing note:** E2-1 through E2-4 are one unit — adding providers
without their credential contracts and cross-field validation produces
configs that pass validation and fail at first token.

### E2-1 Expand the first-class provider set (LLM-01)

- [ ] Extend `Provider` (`config/models.py:31`) with the providers to
      support first-class. Candidates: `azure`, `bedrock`, `vertex-ai`,
      `groq`, `mistral`, `cohere`, `deepseek`, `xai`, `together`,
      `fireworks`, `openrouter`, `huggingface`, `vllm`, `watsonx`.
- [ ] Extend `connectors.py:_llm_model_string` with each provider's
      LiteLLM prefix (the current map covers three).
- [ ] Keep `litellm` as the verbatim escape hatch — it is the
      compatibility guarantee for anything not enumerated.
- [ ] Decide the enum's stability policy: it is a published schema enum,
      so additions are backward compatible but removals are not.

### E2-2 Per-provider credential contracts (SEC-04, LLM-02)

`Llm` today offers exactly one credential pair (`apiKeyEnv`/`apiKeyFile`).
Azure needs endpoint + API version + key; Bedrock needs AWS region and
either static keys, a profile, or instance role.

- [ ] Model multi-field credentials without breaking the single-key
      shape (a per-provider sub-block, or reuse `llm.extra` with
      validation).
- [ ] Route every new secret through `SecretResolver` so SEC-04
      file-over-env and rotation semantics hold.
- [ ] Verify SEC-02 redaction covers the new key names
      (`is_sensitive_key` is suffix-based — check `awsSecretAccessKey`,
      `azureApiKey`).
- [ ] Note: Bedrock via LiteLLM pulls in `boto3` — a STACK-01 manifest +
      lock change, subject to the CNT-12 vulnerability gate.

### E2-3 A generic OpenAI-compatible provider

vLLM, LM Studio, OpenRouter, Together and most self-hosted servers are
OpenAI-compatible; one provider covers them all.

- [ ] Add `openai-compatible` requiring `baseUrl` (validated, CFG-14).
- [ ] Confirm the existing `baseUrl` → `base_url` kwarg path
      (`connectors.py:146`) is correct for LiteLLM in this mode.
- [ ] Tests: a local mock server (`scripts/mock_openai_server.py` already
      exists) exercises the path with no network.

### E2-4 Per-provider cross-field validation (CFG-14, CAP-01)

Only `ollama` currently has a documented required-`baseUrl` rule.

- [ ] Table-driven required/forbidden fields per provider.
- [ ] Fail at boot (exit 78) with the offending path, never at runtime.
- [ ] Gate genuinely unproven providers behind CAP-01 until a test
      exists, rather than shipping them silently.
- [ ] Tests: one invalid-config case per provider.

### E2-5 Model capability registry

`llm.contextWindowTokens` (used by ENG-04 history trimming,
`engine/context.py:52`) must be set by hand per model, and nothing
records whether a model supports tool calling, streaming, or vision.

- [ ] Ship a per-model capability table (context window, tools,
      streaming, vision, structured output) with config override.
- [ ] Default `contextWindowTokens` from it so ENG-04 trimming works
      without manual tuning.
- [ ] Reject configs requesting unsupported capabilities (e.g. MCP tools
      on a model with no tool calling) at boot.
- [ ] Decide the refresh policy — a stale table is worse than none.

### E2-6 Default pricing catalog (COST-01)

`costs.models` is an empty list by default, so every deployment must
hand-enter prices or accept the flat defaults.

- [ ] Ship a default price catalog keyed by provider/model.
- [ ] Add a refresh script under `scripts/` and document the provenance
      and update cadence.
- [ ] Keep explicit config overriding the catalog.
- [ ] Tests: catalog hit, catalog miss falling back to defaults, override.

### E2-7 Per-sub-agent cost pricing (closes deferred MA-02)

- [ ] Price each sub-agent against its own `llm.model` instead of the
      root model (`runner.py:_cost_usd` reads `self._applied.llm_model`).
- [ ] Land the P2 cost tests this was deferred behind.
- [ ] Remove the limitation note from `docs/deployment.md`.

### E2-8 Model fallback chains — decide in or out

- [ ] Decide whether an ordered fallback list belongs in scope. It
      interacts with LLM-03 (`RetryableLlm` never replays after a delta)
      and with COST-01 attribution, so it is a requirements change, not a
      config addition.
- [ ] If in scope: define fallback triggers, cost attribution across
      models, and the `x_agent_status` surfaced to the client.

### E2-9 Embedding provider parity (RAG-01)

`RagEmbeddingProvider` is `gemini | openai` only
(`config/models.py:343`), so RAG cannot follow the expanded LLM matrix.

- [ ] Extend the embedding provider set to match E2-1 where LiteLLM
      supports embeddings.
- [ ] Validate embedding dimension against the store's configured
      dimension at boot — this also interlocks with **R-16** (search does
      not filter by `embedding_model`); do R-16 first.

### E2-10 Verification and documentation

- [ ] A provider-matrix test that asserts the LiteLLM model string and
      kwargs per provider with a mocked bridge — no network, no keys.
- [ ] Extend `tests/test_config/test_validation.py` for the new
      cross-field rules.
- [ ] `docs/decisions.md`: the provider set, the enum stability policy,
      and the fallback decision.
- [ ] Update `REQUIREMENTS.md` LLM-01/LLM-02 and regenerate
      `docs/traceability.md`.
- [ ] Regenerate `requirements.lock` via `scripts/compile-lock.sh` if any
      provider needs a new dependency; re-run the CNT-12 scan gate.

## Deferred scope (carried forward)

- **Multi-agent per-sub-agent cost pricing** (P2, MA-02) — cost is priced
  against the root `llm.model`; sub-agent `llm.model` overrides are not
  priced per model until P2 cost tests land.  Documented in
  `docs/deployment.md` (Known limitations).
- **NFR-00 image-based release gates** — benchmark/chaos, zero-downtime
  reload proof, and multi-architecture acceptance run against the built
  image at release time (see the checklist in `docs/release.md`).
