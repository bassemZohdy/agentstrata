# Backlog — remaining work

Status: the 2026-08-05 deep-review backlog (P1–P5, incl. the P5-4 finish
line) is **closed** and recorded in [CHANGELOG.md](CHANGELOG.md). The
2026-08-06 review backlog (R-01…R-32) is **closed** — the 27 landed
items and six review-loop follow-ups are in CHANGELOG.md under the
"2026-08-06 review backlog — closed" entry, with per-commit verification
narratives in [docs/review-log.md](docs/review-log.md). This file tracks
only what is **still open**: six review stragglers (below) and the two
planned-scope epics (E1, E2). Resolved decisions live in
[docs/decisions.md](docs/decisions.md); requirement IDs trace to
[REQUIREMENTS.md](REQUIREMENTS.md); build order and rationale are in
[PLAN.md](PLAN.md).

## Open 2026-08-06 review items

### R-03 loose end — the RAG degraded flag is discarded by its only caller

`runner.py:167` binds `_rag_degraded` and never reads it; the branch
still switches on the sentinel string (`rag_context == "degraded"`, set
at `runner.py:408`). The flag is exactly what the R-03 refactor added,
so switch the branch to it and drop the sentinel. Not a live bug — but
the signature `tuple[str | None, bool]` permits `(context, True)` and
`_rag_context` passes that through unchanged (`return context,
degraded`), so a future "degraded store returned partial hits" path
would silently emit no `RagDegraded` event and, under `rag.required`,
would not fail the run (RAG-04).

### R-12 follow-up — the drain snapshots `run_registry` once at drain start

A run that passes the `is_draining()` check just before the flag flips,
then registers its task after the snapshot, is not waited on — when the
snapshotted runs finish early the drain proceeds and
`_cancel_inflight_runs` cancels the newcomer before its grace expires
(CNT-07 says in-flight runs keep their deadline up to
`shutdownGraceSeconds`). The window is the several awaits in `chat.py`
between the draining check and `run_registry.add(...)`. Re-snapshot in a
loop until the registry is stable or the grace expires.

### R-19 follow-up — idempotency admission CapacityError maps to 500, not 503

The R-19 mapping convention is not applied to the chat/ACP idempotency
admission. `create_idempotency` raises `CapacityError` when
`storage.maxIdempotencyRecordsPerSession` is reached (`memory.py:485`,
`redis_backend.py:382,442`), and the R-30 handler maps only
`BackendUnavailableError` → 503; everything else falls to the `except
BaseException` catch-all, which correctly releases the slot but
re-raises, so the client sees **500 `internal_error`** (verified). A
configured capacity limit is a client-visible condition, not a server
bug, and `storage_capacity: 503` is already in `STATUS_BY_CODE`. Map it
there too, in chat and ACP.

### R-21 stragglers — `runner.py` leftovers

- [ ] `runner.py:804` — error message still reads "Approval is a Phase 3
      capability" although P3 approvals shipped; it is emitted for
      `requested_tool_confirmations`.
- [ ] `runner.py:807-821` — `_finalize` takes `state` and `request` and
      uses neither.

(The `health.py:123` `_applied_dump` straggler was folded into R-02 and
is done.)

### R-25 The R-22 backoff tests sit exactly on their assertion's lower bound

*Intelligence: low — test reliability / CI.*

`test_watch_failure_backs_off` and `test_list_failure_backs_off`
(`tests/test_operator/test_operator.py`, added in `a705e22`) let the
operator run for 1.6 s of wall clock and assert
`3 <= kube.list_calls <= 8`.

Measured: `list_calls` is **exactly 3** on every run, for both tests
(4 trials each, both failure modes). The expected value *is* the lower
bound, so the tests have zero margin — any scheduling delay on a loaded
CI runner pushes the third re-list past the stop and yields 2, failing
the build.

The docstring's premise is a miscount: it says the window "admits ~4-5
re-lists", but the backoff sleeps are 0.25 / 0.5 / 1.0 s, so re-lists
land at t ≈ 0, 0.25, 0.75 and the fourth at ≈1.75 s — after the 1.6 s
stop. The author chose the bound believing there was 1-2 calls of
headroom that does not exist.

This does not affect production code — the R-22 backoff itself is
correct and the assertion's *upper* bound is what guards the hot-loop
regression.

- [ ] Drop the lower bound (or set it to 1): it guards nothing — the
      hot-loop regression is caught by the upper bound alone.
- [ ] Preferred: make the test deterministic by injecting the sleep/clock
      into `_backoff` instead of racing wall time, which also removes
      ~3.2 s from the suite.
- [ ] Fix the docstring's "~4-5 re-lists" arithmetic either way.

### R-33 `temperature` / `max_tokens` overrides are accepted but never applied (API-12)

*Intelligence: medium — localized correctness + a spec decision.*

R-20 removed the `RunConfig(temperature=…, max_output_tokens=…)` kwargs
from `_run_config`. That fixed a **real latent bug** — verified:
`RunConfig(temperature=0.1)` raises `ValidationError` on the current
google-adk, so before this commit *every* run carrying an override
failed with `provider_error`. Removing them is a genuine improvement.

But the override is now validated, bounds-clamped, gated per
`engine.overrides.*` — and then discarded. Verified end to end with
`allowTemperature: true`:

| | Observed |
| --- | --- |
| `POST /v1/chat/completions` with `temperature: 0.1` | **200**, `finish_reason: stop` |
| effect on the provider call | none — the configured default is used |
| any warning / field / header telling the client | **none** |

So API-12's contract ("overrides gating") is half-honoured: the *gate*
works, the *override* does not. A caller tuning temperature gets a clean
200 and silently different sampling. The deviation is recorded only in a
code comment in `runner.py` — `docs/decisions.md` was not touched.

Note the comment's premise ("not expressible until google-adk exposes
the seam") may be too pessimistic: `LlmRequest` *does* carry a `config`
field, and the `RetryableLlm` wrapper already sits in the call path, so a
per-request seam plausibly exists. Worth an hour's investigation before
accepting the deviation as permanent.

- [ ] Pick one and record it in `docs/decisions.md` (a code comment is
      not a decision record for a public API deviation):
      **(a)** apply the override via `LlmRequest.config` / the
      `RetryableLlm` seam — becomes engine work, not a localized fix;
      **(b)** reject the override with a clear 400 when it cannot be
      honoured, so callers are never silently ignored;
      **(c)** amend API-12 in REQUIREMENTS.md to say overrides are
      validated-and-ignored, and say so in the API docs.
- [ ] Whichever way: the current state (silent no-op) should not survive,
      because it is indistinguishable from working.
- [ ] Tests: assert the chosen behaviour explicitly — today nothing
      covers "an override actually changed the call".

## Review log

The 2026-08-06 monitoring-loop history (per-commit findings and
verification narratives, `9408092` → `d28756c`) lives in
[docs/review-log.md](docs/review-log.md). New passes append there.

---

# Planned scope — minimal configuration + full model coverage


Requested 2026-08-06. Two epics. **Both are requirements-impacting**:
they change documented surfaces (CFG-07/CFG-08 env binding, LLM-01
provider set), so each needs a REQUIREMENTS.md amendment and a
`docs/decisions.md` entry *before* implementation, per the project's own
change policy. Neither is a pure dependency bump.

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
