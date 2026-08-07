# Backlog — remaining work

Status: the 2026-08-05 deep-review backlog (P1–P5, incl. the P5-4 finish
line) is **closed** and recorded in [CHANGELOG.md](CHANGELOG.md). The
2026-08-06 review backlog (R-01…R-32) is **closed** — the 27 landed
items and six review-loop follow-ups are in CHANGELOG.md under the
"2026-08-06 review backlog — closed" entry, with per-commit verification
narratives in [docs/review-log.md](docs/review-log.md). This file tracks
only what is **still open**: the two planned-scope epics (E1, E2) — the
six review stragglers (R-03/R-12/R-19/R-21/R-25/R-33) were closed on
2026-08-07. Resolved decisions live in
[docs/decisions.md](docs/decisions.md); requirement IDs trace to
[REQUIREMENTS.md](REQUIREMENTS.md); build order and rationale are in
[PLAN.md](PLAN.md).

## Open 2026-08-06 review items (all closed)

The six review stragglers tracked below were closed on 2026-08-07 (see
CHANGELOG); their sections remain as the record of what was done.

### R-03 loose end — the RAG degraded flag is discarded by its only caller

- [x] Done: `_execute_inner` switches on the `rag_degraded` flag and
      `_rag_context` returns `(context, degraded)` with no sentinel
      string; `_new_message`'s `!= "degraded"` special case removed (a
      degraded store yields `None` context, so the context block is
      naturally omitted).  The `(context, True)` partial-hits path now
      correctly emits `RagDegraded` / fails under `rag.required`
      (RAG-04).  Covered by the existing RagDegraded/rag_unavailable
      tests + the full suite.

### R-12 follow-up — the drain snapshots `run_registry` once at drain start

- [x] Done: `_drain_after_grace` re-snapshots in a loop — after a wait
      completes it re-checks the registry and waits again for any
      late-registered run, bounded by the overall grace deadline (the
      chat.py is_draining() -> run_registry.add() window is covered).  A
      registry empty at drain start still gets the full grace window for
      in-flight non-run requests.  Test:
      `test_drain_waits_for_late_registered_run` — a run registering
      10 ms after the first snapshot is waited on to completion, never
      cancelled early.

### R-19 follow-up — idempotency admission CapacityError maps to 500, not 503

- [x] Done: chat + ACP admission map `CapacityError` -> 503
      `storage_capacity` (slot released first, same shape as the R-30
      handler).  Tests: `TestIdempotencyCapacity` — chat + ACP with a
      capacity-raising backend return 503 `storage_capacity` and
      `_in_flight` returns to 0.

### R-21 stragglers — `runner.py` leftovers

- [x] `runner.py:804` — message now reads "tool-call approval required
      (approval gate)".  Done.
- [x] `runner.py:807-821` — `_finalize(limiter)` — the unused `state`,
      `text_parts`, and `request` parameters removed from the signature
      and the call site.  Done.

(The `health.py:123` `_applied_dump` straggler was folded into R-02 and
is done.)

### R-25 The R-22 backoff tests sit exactly on their assertion's lower bound

- [x] Done (preferred option): `_backoff` accepts an injectable
      `sleeper` (threaded through `run_operator` as `backoff_sleeper`);
      both tests now drive a recording fake sleep instead of wall time —
      deterministic (no lower-bound flake on a loaded CI runner) and
      ~3.2 s faster.  Assertions: exact cycle counts (3 list calls, stop
      fires during the 3rd backoff) + per-step backoff lower bounds
      (0.25/0.5/1.0 for consecutive list failures; the watch path
      correctly stays at the 0.25 s base since the failure counter
      resets on each successful re-list).  Docstring arithmetic fixed in
      the rewrite.

### R-33 `temperature` / `max_tokens` overrides are accepted but never applied (API-12)

- [x] Done (option a): overrides are APPLIED to the provider call — the
      seam existed after all (`LlmRequest.config` is a
      `GenerateContentConfig` with `temperature`/`max_output_tokens`;
      ADK merges `RunConfig.labels` into the per-step request's labels,
      and `RetryableLlm` is already in the call path).  The values ride
      in `RunConfig.labels` and the wrapper applies + strips them, so
      concurrent runs never share override state.  Decision recorded in
      `docs/decisions.md` (R-33).  As a necessary guard now that values
      flow to the provider: above-cap / non-finite overrides return 400
      `override_not_allowed` (API-12), never silently clamped.
- [x] Silent no-op eliminated: an override demonstrably changes the
      provider call (tests below).
- [x] Tests: `tests/test_engine/test_overrides.py` (temperature /
      max_tokens reach the provider config; synthetic labels stripped;
      no-override untouched) + `TestOverrideApplication` in test_api.py
      (end-to-end 200 with the applied config; above-cap temperature and
      max_tokens -> 400 `override_not_allowed`).

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
