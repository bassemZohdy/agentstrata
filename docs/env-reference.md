# Agentbase environment-variable reference (CFG-17)

Schema-derived from `AgentConfig`; regenerate with `scripts/gen-env-reference.py` (CI zero-diff).
Aliases lose to canonical names (CFG-07); collection items are not env-bindable — use `AGENT_APPLICATION_JSON` (CFG-08).

| Variable | Path | Type | Default | Secret |
| --- | --- | --- | --- | --- |
| `AGENT_SCHEMA_VERSION` | `schemaVersion` | integer | `1` |  |
| `AGENT_NAME` | `name` | string | `required` |  |
| `AGENT_DESCRIPTION` | `description` | string | `` |  |
| `AGENT_PROFILE` | `profile` | string | `` |  |
| `AGENT_ENGINE` | `engine` | model (JSON object) | `required` |  |
| `AGENT_ENGINE_SYSTEM_INSTRUCTION` (alias: AGENT_INSTRUCTION) | `engine.systemInstruction` | string | `required` |  |
| `AGENT_ENGINE_TEMPERATURE` | `engine.temperature` | number | `0.7` |  |
| `AGENT_ENGINE_TOP_P` | `engine.topP` | number | `1.0` |  |
| `AGENT_ENGINE_MAX_TOKENS` | `engine.maxTokens` | integer | `4096` |  |
| `AGENT_ENGINE_MAX_OUTPUT_BYTES` | `engine.maxOutputBytes` | integer | `1048576` |  |
| `AGENT_ENGINE_TIMEOUT_SECONDS` | `engine.timeoutSeconds` | integer | `300` |  |
| `AGENT_ENGINE_MAX_ITERATIONS` | `engine.maxIterations` | integer | `10` |  |
| `AGENT_ENGINE_HISTORY_MAX_MESSAGES` | `engine.historyMaxMessages` | integer | `200` |  |
| `AGENT_ENGINE_HISTORY_MAX_BYTES` | `engine.historyMaxBytes` | integer | `4194304` |  |
| `AGENT_ENGINE_STREAMING` | `engine.streaming` | StreamingMode | `text` |  |
| `AGENT_ENGINE_OVERRIDES` | `engine.overrides` | model (JSON object) | `{"allowMaxTokens":true,"allowTemperature":true,"maxTokensMax":8192,"temperatureMax":2.0}` |  |
| `AGENT_ENGINE_OVERRIDES_ALLOW_TEMPERATURE` | `engine.overrides.allowTemperature` | boolean | `true` |  |
| `AGENT_ENGINE_OVERRIDES_ALLOW_MAX_TOKENS` | `engine.overrides.allowMaxTokens` | boolean | `true` |  |
| `AGENT_ENGINE_OVERRIDES_TEMPERATURE_MAX` | `engine.overrides.temperatureMax` | number | `2.0` |  |
| `AGENT_ENGINE_OVERRIDES_MAX_TOKENS_MAX` | `engine.overrides.maxTokensMax` | integer | `8192` |  |
| `AGENT_ENGINE_TOKEN_BUDGET` | `engine.tokenBudget` | model (JSON object) | `{"perRequest":0,"perSession":0}` |  |
| `AGENT_ENGINE_TOKEN_BUDGET_PER_REQUEST` | `engine.tokenBudget.perRequest` | integer | `0` |  |
| `AGENT_ENGINE_TOKEN_BUDGET_PER_SESSION` | `engine.tokenBudget.perSession` | integer | `0` |  |
| `AGENT_LLM` | `llm` | model (JSON object) | `required` |  |
| `AGENT_LLM_PROVIDER` (alias: AGENT_PROVIDER) | `llm.provider` | Provider | `gemini` |  |
| `AGENT_LLM_MODEL` (alias: AGENT_MODEL) | `llm.model` | string | `required` |  |
| `AGENT_LLM_API_KEY_ENV` (alias: AGENT_API_KEY) | `llm.apiKeyEnv` | string | `null` | yes |
| `AGENT_LLM_API_KEY_FILE` | `llm.apiKeyFile` | string | `null` | yes |
| `AGENT_LLM_AUTO_API_KEY_ENV` | `llm.autoApiKeyEnv` | boolean | `false` | yes |
| `AGENT_LLM_BASE_URL` | `llm.baseUrl` | string | `` |  |
| `AGENT_LLM_CONTEXT_WINDOW_TOKENS` | `llm.contextWindowTokens` | integer | `0` |  |
| `AGENT_LLM_VERTEX` | `llm.vertex` | model (JSON object) | `{"enabled":false,"location":"us-central1","project":""}` |  |
| `AGENT_LLM_VERTEX_ENABLED` | `llm.vertex.enabled` | boolean | `false` |  |
| `AGENT_LLM_VERTEX_PROJECT` | `llm.vertex.project` | string | `` |  |
| `AGENT_LLM_VERTEX_LOCATION` | `llm.vertex.location` | string | `us-central1` |  |
| `AGENT_LLM_EXTRA` | `llm.extra` | map (JSON object) | `{}` |  |
| `AGENT_TOOLS` | `tools` | model (JSON object) | `{"mcpServers":[]}` |  |
| `AGENT_TOOLS_MCP_SERVERS` | `tools.mcpServers` | list (JSON array) | `[]` |  |
| `AGENT_STORAGE` | `storage` | model (JSON object) | `{"connectionStringEnv":null,"connectionStringFile":null,"idempotencyTtlSeconds":86400,"lockAcquireSeconds":0.0,"maxIdempotencyRecordsPerSession":1000,"maxRunsPerSession":1000,"maxSessions":10000,"path":"","runTtlSeconds":604800,"sessionTtlSeconds":86400,"sweepIntervalSeconds":60,"type":"memory"}` |  |
| `AGENT_STORAGE_TYPE` | `storage.type` | StorageType | `memory` |  |
| `AGENT_STORAGE_PATH` | `storage.path` | string | `` |  |
| `AGENT_STORAGE_CONNECTION_STRING_ENV` | `storage.connectionStringEnv` | string | `null` | yes |
| `AGENT_STORAGE_CONNECTION_STRING_FILE` | `storage.connectionStringFile` | string | `null` | yes |
| `AGENT_STORAGE_SESSION_TTL_SECONDS` | `storage.sessionTtlSeconds` | integer | `86400` |  |
| `AGENT_STORAGE_RUN_TTL_SECONDS` | `storage.runTtlSeconds` | integer | `604800` |  |
| `AGENT_STORAGE_MAX_SESSIONS` | `storage.maxSessions` | integer | `10000` |  |
| `AGENT_STORAGE_MAX_RUNS_PER_SESSION` | `storage.maxRunsPerSession` | integer | `1000` |  |
| `AGENT_STORAGE_MAX_IDEMPOTENCY_RECORDS_PER_SESSION` | `storage.maxIdempotencyRecordsPerSession` | integer | `1000` |  |
| `AGENT_STORAGE_LOCK_ACQUIRE_SECONDS` | `storage.lockAcquireSeconds` | number | `0.0` |  |
| `AGENT_STORAGE_IDEMPOTENCY_TTL_SECONDS` | `storage.idempotencyTtlSeconds` | integer | `86400` |  |
| `AGENT_STORAGE_SWEEP_INTERVAL_SECONDS` | `storage.sweepIntervalSeconds` | integer | `60` |  |
| `AGENT_SERVER` | `server` | model (JSON object) | `{"auth":{"apiKeyEnv":null,"apiKeyFile":null,"jwt":{"audience":"","issuer":"","jwksUrl":"","principalClaim":"sub","refreshSeconds":3600,"timeoutSeconds":5},"mode":"none"},"corsAllowCredentials":false,"corsOrigins":["*"],"exposeSystemInstruction":false,"host":"0.0.0.0","maxConcurrentRequests":100,"maxHeaderBytes":32768,"maxHeaderCount":100,"maxMessageBytes":262144,"maxRequestBytes":1048576,"maxRequestLineBytes":8192,"port":8080,"protocols":{"acp":false,"openaiCompat":true,"websocket":false},"rateLimit":{"enabled":false,"requestsPerMinute":60},"shutdownGraceSeconds":25,"slowConsumerSeconds":10,"streamQueueEvents":64,"trustedProxyCidrs":[]}` |  |
| `AGENT_SERVER_HOST` | `server.host` | string | `0.0.0.0` |  |
| `AGENT_SERVER_PORT` | `server.port` | integer | `8080` |  |
| `AGENT_SERVER_PROTOCOLS` | `server.protocols` | model (JSON object) | `{"acp":false,"openaiCompat":true,"websocket":false}` |  |
| `AGENT_SERVER_PROTOCOLS_OPENAI_COMPAT` | `server.protocols.openaiCompat` | boolean | `true` |  |
| `AGENT_SERVER_PROTOCOLS_ACP` | `server.protocols.acp` | boolean | `false` |  |
| `AGENT_SERVER_PROTOCOLS_WEBSOCKET` | `server.protocols.websocket` | boolean | `false` |  |
| `AGENT_SERVER_CORS_ORIGINS` | `server.corsOrigins` | list (JSON array) | `["*"]` |  |
| `AGENT_SERVER_CORS_ALLOW_CREDENTIALS` | `server.corsAllowCredentials` | boolean | `false` |  |
| `AGENT_SERVER_AUTH` | `server.auth` | model (JSON object) | `{"apiKeyEnv":null,"apiKeyFile":null,"jwt":{"audience":"","issuer":"","jwksUrl":"","principalClaim":"sub","refreshSeconds":3600,"timeoutSeconds":5},"mode":"none"}` |  |
| `AGENT_SERVER_AUTH_MODE` | `server.auth.mode` | AuthMode | `none` |  |
| `AGENT_SERVER_AUTH_API_KEY_ENV` | `server.auth.apiKeyEnv` | string | `null` | yes |
| `AGENT_SERVER_AUTH_API_KEY_FILE` | `server.auth.apiKeyFile` | string | `null` | yes |
| `AGENT_SERVER_AUTH_JWT` | `server.auth.jwt` | model (JSON object) | `{"audience":"","issuer":"","jwksUrl":"","principalClaim":"sub","refreshSeconds":3600,"timeoutSeconds":5}` |  |
| `AGENT_SERVER_AUTH_JWT_ISSUER` | `server.auth.jwt.issuer` | string | `` |  |
| `AGENT_SERVER_AUTH_JWT_AUDIENCE` | `server.auth.jwt.audience` | string | `` |  |
| `AGENT_SERVER_AUTH_JWT_JWKS_URL` | `server.auth.jwt.jwksUrl` | string | `` |  |
| `AGENT_SERVER_AUTH_JWT_PRINCIPAL_CLAIM` | `server.auth.jwt.principalClaim` | string | `sub` |  |
| `AGENT_SERVER_AUTH_JWT_REFRESH_SECONDS` | `server.auth.jwt.refreshSeconds` | integer | `3600` |  |
| `AGENT_SERVER_AUTH_JWT_TIMEOUT_SECONDS` | `server.auth.jwt.timeoutSeconds` | integer | `5` |  |
| `AGENT_SERVER_RATE_LIMIT` | `server.rateLimit` | model (JSON object) | `{"enabled":false,"requestsPerMinute":60}` |  |
| `AGENT_SERVER_RATE_LIMIT_ENABLED` | `server.rateLimit.enabled` | boolean | `false` |  |
| `AGENT_SERVER_RATE_LIMIT_REQUESTS_PER_MINUTE` | `server.rateLimit.requestsPerMinute` | integer | `60` |  |
| `AGENT_SERVER_TRUSTED_PROXY_CIDRS` | `server.trustedProxyCidrs` | list (JSON array) | `[]` |  |
| `AGENT_SERVER_MAX_CONCURRENT_REQUESTS` | `server.maxConcurrentRequests` | integer | `100` |  |
| `AGENT_SERVER_MAX_REQUEST_LINE_BYTES` | `server.maxRequestLineBytes` | integer | `8192` |  |
| `AGENT_SERVER_MAX_HEADER_BYTES` | `server.maxHeaderBytes` | integer | `32768` |  |
| `AGENT_SERVER_MAX_HEADER_COUNT` | `server.maxHeaderCount` | integer | `100` |  |
| `AGENT_SERVER_MAX_REQUEST_BYTES` | `server.maxRequestBytes` | integer | `1048576` |  |
| `AGENT_SERVER_MAX_MESSAGE_BYTES` | `server.maxMessageBytes` | integer | `262144` |  |
| `AGENT_SERVER_STREAM_QUEUE_EVENTS` | `server.streamQueueEvents` | integer | `64` |  |
| `AGENT_SERVER_SLOW_CONSUMER_SECONDS` | `server.slowConsumerSeconds` | integer | `10` |  |
| `AGENT_SERVER_EXPOSE_SYSTEM_INSTRUCTION` | `server.exposeSystemInstruction` | boolean | `false` |  |
| `AGENT_SERVER_SHUTDOWN_GRACE_SECONDS` | `server.shutdownGraceSeconds` | integer | `25` |  |
| `AGENT_K8S` | `k8s` | model (JSON object) | `{"enabled":false,"name":"","namespace":"default","required":false,"resyncSeconds":300}` |  |
| `AGENT_K8S_ENABLED` | `k8s.enabled` | boolean | `false` |  |
| `AGENT_K8S_REQUIRED` | `k8s.required` | boolean | `false` |  |
| `AGENT_K8S_NAMESPACE` | `k8s.namespace` | string | `default` |  |
| `AGENT_K8S_NAME` | `k8s.name` | string | `` |  |
| `AGENT_K8S_RESYNC_SECONDS` | `k8s.resyncSeconds` | integer | `300` |  |
| `AGENT_OBSERVABILITY` | `observability` | model (JSON object) | `{"includeToolArguments":false,"logFormat":"json","logLevel":"INFO","otel":{"enabled":false,"serviceName":""},"prometheus":{"enabled":false,"path":"/metrics"}}` |  |
| `AGENT_OBSERVABILITY_LOG_LEVEL` | `observability.logLevel` | LogLevel | `INFO` |  |
| `AGENT_OBSERVABILITY_LOG_FORMAT` | `observability.logFormat` | LogFormat | `json` |  |
| `AGENT_OBSERVABILITY_INCLUDE_TOOL_ARGUMENTS` | `observability.includeToolArguments` | boolean | `false` |  |
| `AGENT_OBSERVABILITY_OTEL` | `observability.otel` | model (JSON object) | `{"enabled":false,"serviceName":""}` |  |
| `AGENT_OBSERVABILITY_OTEL_ENABLED` | `observability.otel.enabled` | boolean | `false` |  |
| `AGENT_OBSERVABILITY_OTEL_SERVICE_NAME` | `observability.otel.serviceName` | string | `` |  |
| `AGENT_OBSERVABILITY_PROMETHEUS` | `observability.prometheus` | model (JSON object) | `{"enabled":false,"path":"/metrics"}` |  |
| `AGENT_OBSERVABILITY_PROMETHEUS_ENABLED` | `observability.prometheus.enabled` | boolean | `false` |  |
| `AGENT_OBSERVABILITY_PROMETHEUS_PATH` | `observability.prometheus.path` | string | `/metrics` |  |
| `AGENT_AGENTS` | `agents` | list (JSON array) | `[]` |  |
| `AGENT_APPROVAL` | `approval` | model (JSON object) | `{"enabled":false,"onTimeout":"deny","timeoutSeconds":300,"tools":[]}` |  |
| `AGENT_APPROVAL_ENABLED` | `approval.enabled` | boolean | `false` |  |
| `AGENT_APPROVAL_TOOLS` | `approval.tools` | list (JSON array) | `[]` |  |
| `AGENT_APPROVAL_TIMEOUT_SECONDS` | `approval.timeoutSeconds` | integer | `300` |  |
| `AGENT_APPROVAL_ON_TIMEOUT` | `approval.onTimeout` | ApprovalTimeout | `deny` |  |
| `AGENT_RAG` | `rag` | model (JSON object) | `{"chunkChars":1000,"chunkOverlapChars":200,"embedding":{"apiKeyEnv":null,"apiKeyFile":null,"model":"text-embedding-004","provider":"gemini"},"enabled":false,"maxDocumentBytes":10485760,"minScore":0.0,"required":false,"store":{"collection":"agentbase","connectionStringEnv":null,"connectionStringFile":null,"options":{},"type":"chroma"},"topK":5}` |  |
| `AGENT_RAG_ENABLED` | `rag.enabled` | boolean | `false` |  |
| `AGENT_RAG_REQUIRED` | `rag.required` | boolean | `false` |  |
| `AGENT_RAG_STORE` | `rag.store` | model (JSON object) | `{"collection":"agentbase","connectionStringEnv":null,"connectionStringFile":null,"options":{},"type":"chroma"}` |  |
| `AGENT_RAG_STORE_TYPE` | `rag.store.type` | RagStoreType | `chroma` |  |
| `AGENT_RAG_STORE_CONNECTION_STRING_ENV` | `rag.store.connectionStringEnv` | string | `null` | yes |
| `AGENT_RAG_STORE_CONNECTION_STRING_FILE` | `rag.store.connectionStringFile` | string | `null` | yes |
| `AGENT_RAG_STORE_COLLECTION` | `rag.store.collection` | string | `agentbase` |  |
| `AGENT_RAG_STORE_OPTIONS` | `rag.store.options` | map (JSON object) | `{}` |  |
| `AGENT_RAG_EMBEDDING` | `rag.embedding` | model (JSON object) | `{"apiKeyEnv":null,"apiKeyFile":null,"model":"text-embedding-004","provider":"gemini"}` |  |
| `AGENT_RAG_EMBEDDING_PROVIDER` | `rag.embedding.provider` | RagEmbeddingProvider | `gemini` |  |
| `AGENT_RAG_EMBEDDING_MODEL` | `rag.embedding.model` | string | `text-embedding-004` |  |
| `AGENT_RAG_EMBEDDING_API_KEY_ENV` | `rag.embedding.apiKeyEnv` | string | `null` | yes |
| `AGENT_RAG_EMBEDDING_API_KEY_FILE` | `rag.embedding.apiKeyFile` | string | `null` | yes |
| `AGENT_RAG_TOP_K` | `rag.topK` | integer | `5` |  |
| `AGENT_RAG_MIN_SCORE` | `rag.minScore` | number | `0.0` |  |
| `AGENT_RAG_CHUNK_CHARS` | `rag.chunkChars` | integer | `1000` |  |
| `AGENT_RAG_CHUNK_OVERLAP_CHARS` | `rag.chunkOverlapChars` | integer | `200` |  |
| `AGENT_RAG_MAX_DOCUMENT_BYTES` | `rag.maxDocumentBytes` | integer | `10485760` |  |
| `AGENT_COSTS` | `costs` | model (JSON object) | `{"defaultInputPerMillion":0.0,"defaultOutputPerMillion":0.0,"enabled":false,"models":[]}` |  |
| `AGENT_COSTS_ENABLED` | `costs.enabled` | boolean | `false` |  |
| `AGENT_COSTS_DEFAULT_INPUT_PER_MILLION` | `costs.defaultInputPerMillion` | number | `0.0` |  |
| `AGENT_COSTS_DEFAULT_OUTPUT_PER_MILLION` | `costs.defaultOutputPerMillion` | number | `0.0` |  |
| `AGENT_COSTS_MODELS` | `costs.models` | list (JSON array) | `[]` |  |
