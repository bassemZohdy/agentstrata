# REQUIREMENTS 2.7 amendment — E2 provider coverage.
p = "REQUIREMENTS.md"
t = open(p, encoding="utf-8").read()

# history entry
old = "| 2.6 | E1 env-first configuration:"
new = """| 2.7 | E2 provider coverage: LLM-01 first-class set extended to twelve LiteLLM-native providers (`azure`, `groq`, `mistral`, `cohere`, `deepseek`, `xai`, `together`, `fireworks`, `openrouter`, `huggingface`, `vllm`, `watsonx`) with their model-string prefixes; LLM-01a publishes the enum stability policy (additions backward-compatible via amendment, removals schema-major); CFG-14 requires `baseUrl` for `vllm` (the generic OpenAI-compatible provider, E2-3) and `azure`; `bedrock`/`vertex-ai` deferred behind the STACK-01 lock gate (E2-2); LLM-03 keeps \"no cross-model fallback\" — E2-8 fallback chains decided out of scope with a recorded deferral |
| 2.6 | E1 env-first configuration:"""
assert old in t, "history anchor"
t = t.replace(old, new, 1)

# LLM-01 provider list
old = """| `provider` | enum | `"gemini"` | `gemini \\| openai \\| anthropic \\| ollama \\| litellm` |"""
new = """| `provider` | enum | `"gemini"` | `gemini \\| openai \\| anthropic \\| ollama \\| litellm \\| azure \\| groq \\| mistral \\| cohere \\| deepseek \\| xai \\| together \\| fireworks \\| openrouter \\| huggingface \\| vllm \\| watsonx` (LLM-01a stability) |"""
assert old in t, "provider row anchor"
t = t.replace(old, new, 1)

# LLM-01 body: prefixes + openai-compatible + stability
old = """**LLM-01** — `provider: gemini` with `vertex.enabled: false` MUST use ADK's native Gemini model with the API key. `vertex.enabled: true` MUST use ADC (no key required). All other providers MUST be constructed via ADK's LiteLLM bridge with the LiteLLM model string formed as: `openai` → `openai/{model}`, `anthropic` → `anthropic/{model}`, `ollama` → `ollama_chat/{model}` (with `api_base = baseUrl`), `litellm` → `{model}` used verbatim (escape hatch for any LiteLLM-supported provider)."""
new = """**LLM-01** — `provider: gemini` with `vertex.enabled: false` MUST use ADK's native Gemini model with the API key. `vertex.enabled: true` MUST use ADC (no key required). All other providers MUST be constructed via ADK's LiteLLM bridge with the LiteLLM model string formed as: `openai` → `openai/{model}`, `anthropic` → `anthropic/{model}`, `ollama` → `ollama_chat/{model}` (with `api_base = baseUrl`), `litellm` → `{model}` used verbatim (escape hatch for any LiteLLM-supported provider), and the E2-1 set — `azure` → `azure/{model}`, `groq` → `groq/{model}`, `mistral` → `mistral/{model}`, `cohere` → `cohere/{model}`, `deepseek` → `deepseek/{model}`, `xai` → `xai/{model}`, `together` → `together_ai/{model}`, `fireworks` → `fireworks_ai/{model}`, `openrouter` → `openrouter/{model}`, `huggingface` → `huggingface/{model}`, `vllm` → `openai/{model}` (OpenAI-compatible, E2-3, with `api_base = baseUrl`), `watsonx` → `watsonx/{model}`.

**LLM-01a (provider-enum stability)** — `Provider` is a published schema enum: adding a provider is backward compatible and requires only a REQUIREMENTS amendment; removing or renaming a provider value is a schema-major breaking change and MUST NOT occur without one. `bedrock`/`vertex-ai` remain deferred behind the STACK-01 lock + CNT-12 gate and the E2-2 credential contracts; the `litellm` escape hatch covers them meanwhile."""
assert old in t, "LLM-01 anchor"
t = t.replace(old, new, 1)

# CFG-14: add vllm/azure baseUrl requirements
old = """    if doc.get("llm.provider") == "ollama" and not doc.get("llm.baseUrl"):
        issue("llm.baseUrl", "ollama provider requires baseUrl")"""
new = """    if doc.get("llm.provider") == "ollama" and not doc.get("llm.baseUrl"):
        issue("llm.baseUrl", "ollama provider requires baseUrl")
    if doc.get("llm.provider") == "vllm" and not doc.get("llm.baseUrl"):
        issue("llm.baseUrl", "vllm (OpenAI-compatible, E2-3) requires baseUrl")
    if doc.get("llm.provider") == "azure" and not doc.get("llm.baseUrl"):
        issue("llm.baseUrl", "azure provider requires baseUrl (endpoint)")"""
assert old in t, "cfg-14 anchor"
t = t.replace(old, new, 1)

open(p, "w", encoding="utf-8", newline="\n").write(t)
print("REQUIREMENTS 2.7 amendment written")
