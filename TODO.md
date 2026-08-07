# Backlog — remaining work

Status: the 2026-08-05 deep-review backlog (P1–P5, incl. the P5-4 finish
line) is **closed** and recorded in [CHANGELOG.md](CHANGELOG.md). The
2026-08-06 review backlog is **fully closed** — R-01…R-32, the six
review-loop follow-ups, and the six stragglers (R-03/R-12/R-19/R-21/
R-25/R-33) all landed; see CHANGELOG.md ("2026-08-06 review backlog —
closed" and "2026-08-06 review stragglers — closed"). Per-commit
verification narratives are in [docs/review-log.md](docs/review-log.md).
This file tracks only what is **still open**: the planned-scope epic
E2 (full provider coverage) below — E1 (env-first configuration) was
completed on 2026-08-07. Resolved decisions live in
[docs/decisions.md](docs/decisions.md); requirement IDs trace to
[REQUIREMENTS.md](REQUIREMENTS.md); build order and rationale are in
[PLAN.md](PLAN.md).

## Review log

The 2026-08-06/07 monitoring-loop history (per-commit findings and
verification narratives, `9408092` → `6af55be`) lives in
[docs/review-log.md](docs/review-log.md). New passes append there.

---

# Planned scope — E1 done (env-first configuration); E2 open (full provider coverage)
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
