# Backlog — remaining work

Status: the 2026-08-05 deep-review backlog (P1–P5, incl. the P5-4 finish
line) is **closed** and recorded in [CHANGELOG.md](CHANGELOG.md). The
2026-08-06 review backlog is **fully closed** — R-01…R-32, the six
review-loop follow-ups, and the six stragglers (R-03/R-12/R-19/R-21/
R-25/R-33) all landed; see CHANGELOG.md ("2026-08-06 review backlog —
closed" and "2026-08-06 review stragglers — closed"). Per-commit
verification narratives are in [docs/review-log.md](docs/review-log.md).
This file tracks what is **still open**: nothing — E1 (env-first
configuration) and E2 (full provider coverage) both landed on
2026-08-07; the sections below remain as the record. Resolved decisions live in
[docs/decisions.md](docs/decisions.md); requirement IDs trace to
[REQUIREMENTS.md](REQUIREMENTS.md); build order and rationale are in
[PLAN.md](PLAN.md).

## Review log

The 2026-08-06/07 monitoring-loop history (per-commit findings and
verification narratives, `9408092` → `6af55be`) lives in
[docs/review-log.md](docs/review-log.md). New passes append there.

---

# Planned scope — E1 + E2 complete (2026-08-07)
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

- [x] Done: `--print-env` (CFG-10b) emits every bindable path with its
      canonical `AGENT_*` name, short aliases, type, default (or
      `required`), and SEC-02 secret marker — schema-derived, no config
      resolution, mutually exclusive with --validate/--dump-config.
      `scripts/gen-env-reference.py` generates `docs/env-reference.md`
      from the SAME catalog code (CFG-17); CI zero-diff step added
      next to the schema gate.  The required-leaf audit (E1-2) is
      visible in the catalog: exactly `name`, `engine`,
      `engine.systemInstruction`, `llm`, `llm.model` are `required`;
      optional secret refs show `null` defaults.

### E1-2 Guarantee and document the minimum viable env set

- [x] Done: tier 1 is now SKIPPED when the bundled `agent.yaml` is
      absent (CFG-16; the release image still ships it per BASE-01), so
      `AGENT_BUNDLED_DIR` pointing at an empty directory boots from the
      three required leaves + credential supplied via env.  Decision
      recorded in `docs/decisions.md` (schema defaults stand alone;
      `name` stays required — SCH-02 treats changing defaults as a
      schema-major change).  Tests: env-only boot (empty bundled dir),
      missing-required-leaf failure, collection config via
      `AGENT_APPLICATION_JSON` from env alone.

### E1-3 Collection config is not env-bindable

- [x] Done: decision (c) — JSON (`AGENT_APPLICATION_JSON`) remains the
      only per-item path; recorded in `docs/decisions.md` + CFG-08.  A
      list-index-shaped `AGENT_*` variable now warns that collection
      items are not env-bindable and names `AGENT_APPLICATION_JSON`.
      CFG-07 ambiguity detection untouched (aliases share the index).
      Tests: signpost warning; multi-server MCP from env alone.

### E1-4 Short aliases for the high-traffic knobs

- [x] Done: closed table `AGENT_MODEL` / `AGENT_INSTRUCTION` /
      `AGENT_API_KEY` / `AGENT_PROVIDER` (CFG-07 item 4, frozen —
      recorded in `docs/decisions.md`).  Canonical names win over
      aliases regardless of OS env order; aliases participate in
      ambiguity detection via the shared binding index.  Tests:
      alias binds, canonical-wins both orders, alias api-key bind,
      unique-bind-no-ambiguity.

### E1-5 Provider key auto-detection (opt-in)

- [x] Done: `llm.autoApiKeyEnv` (default false, LLM-04) — deterministic
      inference table (`gemini` → `GEMINI_API_KEY`, `openai` →
      `OPENAI_API_KEY`, `anthropic` → `ANTHROPIC_API_KEY`; `ollama` /
      `litellm` / vertex-ADC infer nothing).  Inferred-but-absent
      variable fails boot with the variable name (SEC-03 fail-closed,
      CLI + runtime main); explicit refs always win; the OBS-03
      connector log names the variable, never the value.  Tests:
      table per provider, absent → exit 78, present → exit 0, connector
      resolves the inferred name.

### E1-6 Binding diagnostics

- [x] Done: every env-bound leaf's provenance names the SPECIFIC
      variable (`# tier 5: env:AGENT_LLM_MODEL`, aliases name the alias
      var; null resets keep their reset flag) — CFG-18.  Unmatched
      `AGENT_*` warnings get one boot summary line after the individual
      CFG-08 warnings.  Tests: provenance var naming, summary line
      absent/present.

### E1-7 Documentation

- [x] Done: README "Zero-file agent" quick start (aliases + inferred
      credential examples, link to docs/env-reference.md); deployment.md
      env-only configuration section + env-only Kubernetes Deployment
      example; traceability regenerated for CFG-10b/16/17/18 + LLM-04
      (189 IDs mapped).

### E2-1 Expand the first-class provider set (LLM-01)

- [x] Done: `Provider` extended with twelve LiteLLM-native providers —
      `azure`, `groq`, `mistral`, `cohere`, `deepseek`, `xai`,
      `together`, `fireworks`, `openrouter`, `huggingface`, `vllm`,
      `watsonx` (schema regenerated).  `connectors._LLM_MODEL_PREFIX`
      carries each provider's LiteLLM prefix; `litellm` remains the
      verbatim escape hatch.  `bedrock`/`vertex-ai` deliberately
      deferred (boto3/google-cloud deps — STACK-01 + CNT-12 gate; see
      E2-2).  Decision + enum stability policy (LLM-01a: additions
      backward-compatible via amendment, removals schema-major)
      recorded in `docs/decisions.md` + REQUIREMENTS 2.7.
- [x] Tests: provider-enum matrix, model-string matrix (16 providers),
      litellm verbatim passthrough.

### E2-2 Per-provider credential contracts (SEC-04, LLM-02)

- [x] Done: decision — NO per-provider credential sub-block; multi-field
      credentials (azure `api_version`, future bedrock keys) ride the
      existing `llm.extra` passthrough (already reaches the LiteLLM
      kwargs verbatim; a sub-block would duplicate SEC-04 resolution per
      provider).  SEC-02 redaction widened to cover the new key names
      (`accesskey`/`secretkey` suffixes — `awsSecretAccessKey`,
      `awsSecretKey` now mask in dumps; `azureApiKey` was already
      covered).  bedrock/vertex-ai remain deferred (STACK-01 lock +
      CNT-12 gate).  Decision in `docs/decisions.md`.

### E2-3 A generic OpenAI-compatible provider

- [x] Done: `vllm` IS the generic OpenAI-compatible provider (vLLM, LM
      Studio, self-hosted) — model string `openai/{model}` with
      `api_base = baseUrl` (required, CFG-14 table).  A separate
      `openai-compatible` alias was NOT added (would split the
      required-baseUrl validation across two enum values; decision
      recorded).  Tests: vllm requires baseUrl, connector passes
      api_base; the production `openai` + `baseUrl` path against
      `scripts/mock_openai_server.py` is exercised by the image-NFR
      harness (no network).

### E2-4 Per-provider cross-field validation (CFG-14, CAP-01)

- [x] Done: table-driven `_PROVIDER_REQUIRED_BASE_URL` (ollama, vllm,
      azure) — fails at boot (exit 78) with the offending path, never
      at runtime; vertex rules stay (project required, apiKey refs
      forbidden).  Tests: one invalid case per required-field provider
      + the happy path with baseUrl supplied.

### E2-5 Model capability registry

- [x] Done: `app/engine/model_catalog.py` — curated per-model
      capability table (context window, tools, streaming, vision,
      structured output).  `llm.contextWindowTokens: 0` defaults from
      the registry (ENG-04 trimming works without tuning; explicit
      config always wins).  Boot validation rejects MCP tools on a
      registry-known non-tool model (exit 78, offending path); unknown
      models are NEVER rejected (LLM-05 — a stale registry must not
      block deployments).  Refresh policy = the pricing catalog's
      (manual; documented).  Tests: context default/override/unknown,
      tools-gate on/off, unknown-model passthrough.

### E2-6 Default pricing catalog (COST-01)

- [x] Done: `app/engine/pricing.py` — curated catalog keyed
      `(provider, model)` with provenance; lookup chain: exact
      `costs.models` entry -> catalog -> flat defaults.  Refresh script
      `scripts/refresh-pricing.py` (manual, no network in CI, hardened
      against malformed upstream data); explicit config always wins.
      REQUIREMENTS 2.8.  Tests: catalog lookup, exact-beats-catalog-
      beats-defaults, miss falls back, disabled-no-field.

### E2-7 Per-sub-agent cost pricing (closes deferred MA-02)

- [x] Done: usage is attributed per agent (ADK event author) in
      `_convert`; `_cost_usd` prices each agent's tokens with that
      agent's effective `(provider, model)` (deep-merged llm block,
      `AppliedConfig.agent_llm_models`); no-attribution runs price the
      aggregate with the root.  The OBS-05 cost counter keeps the root
      model label (documented).  Deferred-scope MA-02 note removed from
      deployment docs.  Tests: root vs sub-agent pricing, disabled
      invariant.

### E2-8 Model fallback chains — decide in or out

- [x] Done: decided OUT of scope with a recorded deferral
      (`docs/decisions.md` E2-8 + REQUIREMENTS 2.7): fallbacks interact
      with LLM-03 (no replay after a delta) and COST-01 attribution, so
      they are a requirements change, not a config addition.  LLM-03
      keeps "There is no cross-model fallback".

### E2-9 Embedding provider parity (RAG-01)

- [x] Done: `rag.embedding.provider` extended to `azure` / `cohere` /
      `mistral` / `huggingface` / `watsonx` via the LiteLLM embedding
      bridge (`LiteLlmEmbedding`, same model-string prefixes as the LLM
      set; drivers fail closed at construction).  bedrock/vertex remain
      deferred with the LLM set.  Tests: enum set, model-string
      prefixes.

### E2-10 Verification and documentation

- [x] Done: provider-matrix tests (enum + 16-provider model strings,
      E2-1), per-provider invalid configs (E2-4), capability-registry
      and embedding tests (E2-5/E2-9), cost chain + per-agent pricing
      (E2-6/E2-7); `docs/decisions.md` records for E2-1/2/3/8;
      REQUIREMENTS 2.7/2.8/2.9 (LLM-01/01a/05/06, CFG-14, COST-01);
      traceability mapped (LLM-01a/05/06 + CFG-10b/16/17/18, 192 IDs);
      no new dependencies (requirements.lock untouched — bedrock/
      vertex-ai deferred by decision).  642 host tests; ruff + mypy
      clean; schemas/env-reference/traceability deterministic.
