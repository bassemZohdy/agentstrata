# schemas/

Generated artifacts, never hand-edited (REQUIREMENTS.md DEL-02).

| Artifact | Generated from | Introduced |
| --- | --- | --- |
| `agent.schema.json` | Pydantic Agent Definition schema | Milestone 1 |
| `openapi.json` | FastAPI app | Milestone 5 |
| `agent-overlay.schema.json` | Optional-fields variant of the full schema | Milestone 6 |

CI regenerates and zero-diffs each artifact from the moment it is introduced
(see PLAN.md "Cross-cutting rule (DEL-02)").
