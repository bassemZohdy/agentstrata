"""Agent Definition schema (REQUIREMENTS.md §4, SCH-01 – SCH-09).

Pydantic v2 models with the SCH-01 base configuration (camelCase aliases,
``extra="forbid"``, ``strict=True``, ``populate_by_name=True``). External
documents are camelCase and must pass the CFG-13 alias-only shape walk before
``model_validate``; snake_case input is accepted only for direct internal
construction and is never advertised as a public document format.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, TypeGuard, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

SCHEMA_MAJOR = 1
SCHEMA_VERSION = 1
MAX_SOURCE_BYTES = 1 << 20  # CFG-03a: 1 MiB

# SCH-01
BASE_CONFIG = ConfigDict(
    populate_by_name=True,
    alias_generator=to_camel,
    extra="forbid",
    strict=True,
)


class Provider(StrEnum):
    GEMINI = "gemini"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"
    LITELLM = "litellm"
    # E2-1 (LLM-01/LLM-01a): LiteLLM-native first-class providers — no
    # new dependencies; bedrock/vertex-ai stay deferred (E2-2, STACK-01).
    AZURE = "azure"
    GROQ = "groq"
    MISTRAL = "mistral"
    COHERE = "cohere"
    DEEPSEEK = "deepseek"
    XAI = "xai"
    TOGETHER = "together"
    FIREWORKS = "fireworks"
    OPENROUTER = "openrouter"
    HUGGINGFACE = "huggingface"
    VLLM = "vllm"
    WATSONX = "watsonx"


class StreamingMode(StrEnum):
    TEXT = "text"
    EVENTS = "events"
    DEBUG = "debug"


class StorageType(StrEnum):
    MEMORY = "memory"
    FILE = "file"
    REDIS = "redis"
    POSTGRES = "postgres"


class AuthMode(StrEnum):
    NONE = "none"
    API_KEY = "apiKey"
    JWT = "jwt"


class McpTransport(StrEnum):
    STDIO = "stdio"
    SSE = "sse"
    STREAMABLE_HTTP = "streamable-http"
    # SCH-04: deprecated alias, accepted with a warning, normalized to
    # streamable-http by the resolver before cross-field validation.
    HTTP = "http"


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class LogFormat(StrEnum):
    JSON = "json"
    TEXT = "text"


_DNS_1123 = r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"

# Secret ref pair fields are two optional non-empty strings (SEC-04):
# min_length=1 rejects an empty ref while None means unset.
_SECRET_REF = Field(default=None, min_length=1)


class EngineOverrides(BaseModel):
    model_config = BASE_CONFIG

    allowTemperature: bool = True
    allowMaxTokens: bool = True
    temperatureMax: float = Field(default=2.0, ge=0.0, le=2.0)
    maxTokensMax: int = Field(default=8192, ge=1, le=1_000_000)


class TokenBudget(BaseModel):
    model_config = BASE_CONFIG

    perRequest: int = Field(default=0, ge=0, le=1_000_000_000_000)
    perSession: int = Field(default=0, ge=0, le=1_000_000_000_000)


class Engine(BaseModel):
    model_config = BASE_CONFIG

    systemInstruction: str = Field(min_length=1)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    topP: float = Field(default=1.0, gt=0.0, le=1.0)
    maxTokens: int = Field(default=4096, ge=1, le=1_000_000)
    maxOutputBytes: int = Field(default=1_048_576, ge=1, le=16_777_216)
    timeoutSeconds: int = Field(default=300, ge=1, le=3600)
    maxIterations: int = Field(default=10, ge=1, le=1000)
    historyMaxMessages: int = Field(default=200, ge=1, le=10_000)
    historyMaxBytes: int = Field(default=4_194_304, ge=1024, le=67_108_864)
    streaming: StreamingMode = Field(default=StreamingMode.TEXT, strict=False)
    overrides: EngineOverrides = Field(default_factory=EngineOverrides)
    tokenBudget: TokenBudget = Field(default_factory=TokenBudget)


class VertexConfig(BaseModel):
    model_config = BASE_CONFIG

    enabled: bool = False
    project: str = ""
    location: str = "us-central1"


# LLM-04 (E1-5): deterministic per-provider credential-variable names for
# opt-in inference (llm.autoApiKeyEnv). Providers without a key contract
# are absent from the table.
INFERRED_API_KEY_ENV: dict[str, str] = {
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


class Llm(BaseModel):
    model_config = BASE_CONFIG

    provider: Provider = Field(default=Provider.GEMINI, strict=False)
    model: str = Field(min_length=1, max_length=256)
    apiKeyEnv: str | None = _SECRET_REF
    apiKeyFile: str | None = _SECRET_REF
    autoApiKeyEnv: bool = Field(default=False)
    baseUrl: str = ""
    contextWindowTokens: int = Field(default=0, ge=0)
    vertex: VertexConfig = Field(default_factory=VertexConfig)
    # CFG-13: the only passthrough maps are llm.extra and rag.store.options.
    extra: dict[str, Any] = Field(default_factory=dict)


class SecretHeaderRef(BaseModel):
    model_config = BASE_CONFIG

    env: str | None = _SECRET_REF
    file: str | None = _SECRET_REF
    prefix: str = ""


class ToolFilter(BaseModel):
    model_config = BASE_CONFIG

    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)


class McpServer(BaseModel):
    model_config = BASE_CONFIG

    name: str = Field(pattern=_DNS_1123)
    transport: McpTransport = Field(strict=False)
    url: str = ""
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    secretHeaders: dict[str, SecretHeaderRef] = Field(default_factory=dict)
    authTokenEnv: str | None = _SECRET_REF
    authTokenFile: str | None = _SECRET_REF
    required: bool = False
    toolFilter: ToolFilter = Field(default_factory=ToolFilter)
    connectTimeoutSeconds: int = Field(default=10, gt=0)
    timeoutSeconds: int = Field(default=30, gt=0)
    maxTools: int = Field(default=128, ge=1, le=1000)
    maxTransportMessageBytes: int = Field(default=1_048_576, ge=4096, le=16_777_216)
    maxResultBytes: int = Field(default=100_000, ge=1, le=4_194_304)


class Tools(BaseModel):
    model_config = BASE_CONFIG

    mcpServers: list[McpServer] = Field(default_factory=list)


class AgentDef(BaseModel):
    """MA-01: one root-owned sub-agent (P2). One flat level — AgentDef has
    no agents field, so nested/cyclic definitions are structurally impossible
    (extra="forbid" rejects unknown fields)."""

    model_config = BASE_CONFIG

    name: str = Field(pattern=_DNS_1123)
    systemInstruction: str = Field(min_length=1)
    description: str = Field(default="", max_length=2000)
    # Optional full llm block; inherited from the root by deep merge (MA-02).
    llm: Llm | None = None
    # None = every configured MCP server (resolved at validation, MA-01).
    toolServers: list[str] | None = None


class Storage(BaseModel):
    model_config = BASE_CONFIG

    type: StorageType = Field(default=StorageType.MEMORY, strict=False)
    path: str = ""
    connectionStringEnv: str | None = _SECRET_REF
    connectionStringFile: str | None = _SECRET_REF
    sessionTtlSeconds: int = Field(default=86400, ge=0)
    runTtlSeconds: int = Field(default=604800, ge=60)
    maxSessions: int = Field(default=10000, gt=0)
    maxRunsPerSession: int = Field(default=1000, gt=0)
    maxIdempotencyRecordsPerSession: int = Field(default=1000, gt=0)
    lockAcquireSeconds: float = Field(default=0.0, ge=0.0, le=5.0)
    idempotencyTtlSeconds: int = Field(default=86400, ge=60)
    # SES-06/07 (R-04): how often the lifespan storage sweep runs (TTL
    # expiry, capacity trimming, nonterminal-run reconciliation).
    sweepIntervalSeconds: int = Field(default=60, ge=1, le=86400)


class JwtAuth(BaseModel):
    model_config = BASE_CONFIG

    issuer: str = ""
    audience: str = ""
    jwksUrl: str = ""
    principalClaim: str = "sub"
    refreshSeconds: int = Field(default=3600, ge=60)
    timeoutSeconds: int = Field(default=5, gt=0)


class Auth(BaseModel):
    model_config = BASE_CONFIG

    mode: AuthMode = Field(default=AuthMode.NONE, strict=False)
    apiKeyEnv: str | None = _SECRET_REF
    apiKeyFile: str | None = _SECRET_REF
    jwt: JwtAuth = Field(default_factory=JwtAuth)


class RateLimit(BaseModel):
    model_config = BASE_CONFIG

    enabled: bool = False
    requestsPerMinute: int = Field(default=60, gt=0)


class Protocols(BaseModel):
    model_config = BASE_CONFIG

    openaiCompat: bool = True
    acp: bool = False  # Phase 2; CAP-01 forbids enabling in P1
    websocket: bool = False  # Phase 5; WS-01 /v1/ws


class Server(BaseModel):
    model_config = BASE_CONFIG

    host: str = "0.0.0.0"
    port: int = Field(default=8080, ge=1, le=65535)
    protocols: Protocols = Field(default_factory=Protocols)
    corsOrigins: list[str] = Field(default_factory=lambda: ["*"])
    corsAllowCredentials: bool = False
    auth: Auth = Field(default_factory=Auth)
    rateLimit: RateLimit = Field(default_factory=RateLimit)
    trustedProxyCidrs: list[str] = Field(default_factory=list)
    maxConcurrentRequests: int = Field(default=100, ge=1, le=10_000)
    maxRequestLineBytes: int = Field(default=8192, ge=1024, le=16_384)
    maxHeaderBytes: int = Field(default=32_768, ge=4096, le=131_072)
    maxHeaderCount: int = Field(default=100, ge=1, le=200)
    maxRequestBytes: int = Field(default=1_048_576, ge=1024, le=16_777_216)
    maxMessageBytes: int = Field(default=262_144, ge=1, le=4_194_304)
    streamQueueEvents: int = Field(default=64, ge=1, le=1024)
    slowConsumerSeconds: int = Field(default=10, ge=1, le=300)
    exposeSystemInstruction: bool = False
    shutdownGraceSeconds: int = Field(default=25, ge=1, le=300)


class K8s(BaseModel):
    model_config = BASE_CONFIG

    enabled: bool = False
    required: bool = False
    namespace: str = "default"
    name: str = ""  # default = top-level name; filled by the resolver
    resyncSeconds: int = Field(default=300, ge=30)


class Otel(BaseModel):
    model_config = BASE_CONFIG

    enabled: bool = False
    serviceName: str = ""  # default = top-level name; filled by the resolver


class Prometheus(BaseModel):
    model_config = BASE_CONFIG

    enabled: bool = False
    path: str = Field(default="/metrics", min_length=1, max_length=128)

    @field_validator("path")
    @classmethod
    def _path_must_be_absolute(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("observability.prometheus.path must start with '/'")
        return value


class Observability(BaseModel):
    model_config = BASE_CONFIG

    logLevel: LogLevel = Field(default=LogLevel.INFO, strict=False)
    logFormat: LogFormat = Field(default=LogFormat.JSON, strict=False)
    includeToolArguments: bool = False
    otel: Otel = Field(default_factory=Otel)
    prometheus: Prometheus = Field(default_factory=Prometheus)


# --- SCH-09 phase-gated stubs (fail closed in a P1 build, CAP-01) -----------


class ApprovalTimeout(StrEnum):
    DENY = "deny"
    ALLOW = "allow"


class ApprovalConfig(BaseModel):
    """HITL-01: human-in-the-loop tool approval (P3)."""

    model_config = BASE_CONFIG

    enabled: bool = False
    # Exact server/rawTool or server/* patterns, matched BEFORE public tool
    # renaming (HITL-01).
    tools: list[str] = Field(default_factory=list)
    timeoutSeconds: int = Field(default=300, ge=1, le=86400)
    # "allow" is accepted only when explicitly configured (a startup audit
    # warning is emitted); the default is deny.
    onTimeout: ApprovalTimeout = Field(default=ApprovalTimeout.DENY, strict=False)


class RagStoreType(StrEnum):
    CHROMA = "chroma"
    PGVECTOR = "pgvector"


class RagEmbeddingProvider(StrEnum):
    GEMINI = "gemini"
    OPENAI = "openai"


class RagStore(BaseModel):
    """RAG-01: the vector store; connectionString follows SEC-04 Env/File."""

    model_config = BASE_CONFIG

    type: RagStoreType = Field(default=RagStoreType.CHROMA, strict=False)
    connectionStringEnv: str | None = _SECRET_REF
    connectionStringFile: str | None = _SECRET_REF
    # Chroma collection / pgvector relation: safe backend identifier
    # (DNS-1123, the same family as session IDs).
    collection: str = Field(default="agentbase", pattern=_DNS_1123)
    # rag.store.options is the second explicit passthrough map (CFG-13).
    options: dict[str, Any] = Field(default_factory=dict)


class RagEmbedding(BaseModel):
    """RAG-01: the embedding provider; apiKey follows SEC-04 Env/File."""

    model_config = BASE_CONFIG

    provider: RagEmbeddingProvider = Field(default=RagEmbeddingProvider.GEMINI, strict=False)
    model: str = Field(default="text-embedding-004", min_length=1, max_length=200)
    apiKeyEnv: str | None = _SECRET_REF
    apiKeyFile: str | None = _SECRET_REF


class RagConfig(BaseModel):
    """RAG-01: retrieval-augmented generation (P4, §15)."""

    model_config = BASE_CONFIG

    enabled: bool = False
    required: bool = False
    store: RagStore = Field(default_factory=RagStore)
    embedding: RagEmbedding = Field(default_factory=RagEmbedding)
    topK: int = Field(default=5, ge=1, le=100)
    minScore: float = Field(default=0.0, ge=0.0, le=1.0)
    chunkChars: int = Field(default=1000, ge=1)
    chunkOverlapChars: int = Field(default=200, ge=0)
    maxDocumentBytes: int = Field(default=10485760, ge=1)

    @model_validator(mode="after")
    def _check_overlap(self):
        # RAG-01: overlap < chunk size (a degenerate overlap degenerates the
        # chunk identity).
        if self.chunkOverlapChars >= self.chunkChars:
            raise ValueError("rag.chunkOverlapChars must be smaller than rag.chunkChars")
        return self


class ModelCost(BaseModel):
    model_config = BASE_CONFIG

    model: str = Field(min_length=1, max_length=256)
    inputPerMillion: float = Field(default=0.0, ge=0.0)
    outputPerMillion: float = Field(default=0.0, ge=0.0)


class Costs(BaseModel):
    """COST-01: per-request cost-in-dollars accounting (P5-4).

    Prices are USD per 1M tokens. ``models`` entries override the defaults
    for a specific model name (the exact ``llm.model`` string); when
    ``enabled`` is false no cost is computed anywhere (zero API surface
    change).
    """

    model_config = BASE_CONFIG

    enabled: bool = False
    defaultInputPerMillion: float = Field(default=0.0, ge=0.0)
    defaultOutputPerMillion: float = Field(default=0.0, ge=0.0)
    models: list[ModelCost] = Field(default_factory=list)


class AgentDefinition(BaseModel):
    """The complete Agent Definition document (camelCase, external format)."""

    model_config = BASE_CONFIG

    schema_: str = Field(default="", alias="$schema")
    schemaVersion: int = SCHEMA_VERSION
    name: str = Field(pattern=_DNS_1123)
    description: str = Field(default="", max_length=2000)
    profile: str = ""
    engine: Engine
    llm: Llm
    tools: Tools = Field(default_factory=Tools)
    storage: Storage = Field(default_factory=Storage)
    server: Server = Field(default_factory=Server)
    k8s: K8s = Field(default_factory=K8s, alias="k8s")  # to_camel would yield "k8S"
    observability: Observability = Field(default_factory=Observability)
    agents: list[AgentDef] = Field(default_factory=list)  # P2 (MA-01)
    approval: ApprovalConfig = Field(default_factory=ApprovalConfig)  # P3
    rag: RagConfig = Field(default_factory=RagConfig)  # P4
    costs: Costs = Field(default_factory=Costs)  # P5-4 (COST-01)

    @field_validator("schemaVersion")
    @classmethod
    def _schema_version_must_equal_current(cls, v: int) -> int:
        if v != SCHEMA_VERSION:
            raise ValueError(f"unsupported schemaVersion {v}; expected {SCHEMA_VERSION}")
        return v


# Backwards-compatible alias used across the codebase.
AgentConfig = AgentDefinition


# --- Schema path enumeration (CFG-07 env binding, CFG-11 dump order) ---------


def _camel(name: str) -> str:
    return to_camel(name.replace("_", ""))


def _is_model(t: Any) -> TypeGuard[type[BaseModel]]:
    return isinstance(t, type) and issubclass(t, BaseModel)


def iter_schema_fields(model_cls: type[BaseModel]) -> list[tuple[str, str, Any, bool]]:
    """Enumerate schema paths with their target types.

    Returns ``(dotted_camel_path, kind, annotation, bindable)`` with kind one
    of ``leaf`` / ``model`` / ``list`` / ``passthrough``. ``bindable`` is
    False for paths under list items — CFG-07: list indexes and passthrough
    keys are not bindable via env/CLI; only whole list/model/passthrough
    paths and scalar leaves are.
    """
    fields: list[tuple[str, str, Any, bool]] = []

    def walk(cls: type[BaseModel], prefix: str, bindable: bool) -> None:
        for fname, field in cls.model_fields.items():
            alias = field.alias or _camel(fname)
            path = f"{prefix}.{alias}" if prefix else alias
            ann = field.annotation
            origin = get_origin(ann)
            if origin in (list, dict):
                inner = get_args(ann)[0] if get_args(ann) else None
                kind = "passthrough" if origin is dict else "list"
                fields.append((path, kind, ann, bindable))
                if origin is list and _is_model(inner):
                    walk(inner, path, False)  # list-item fields: not bindable
            elif _is_model(ann):
                fields.append((path, "model", ann, bindable))
                walk(ann, path, bindable)
            else:
                fields.append((path, "leaf", ann, bindable))

    walk(model_cls, "", True)
    return fields


def iter_schema_paths(model_cls: type[BaseModel]) -> list[tuple[str, str]]:
    """Bindable paths without annotations (see :func:`iter_schema_fields`)."""
    return [(p, k) for p, k, _, _ in iter_schema_fields(model_cls)]


def field_order(model_cls: type[BaseModel]) -> list[str]:
    """camelCase aliases in schema field order (CFG-11 canonical dump)."""
    return [f.alias or _camel(n) for n, f in model_cls.model_fields.items()]


def field_aliases(model_cls: type[BaseModel]) -> set[str]:
    """All accepted external (camelCase) keys for a model.

    The CFG-13 shape walk accepts ONLY these aliases — Python field names
    (snake_case) are rejected on external documents.
    """
    return {f.alias or _camel(n) for n, f in model_cls.model_fields.items()}
