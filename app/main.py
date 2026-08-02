"""AgentStrata entrypoint — fixed module path per REQUIREMENTS.md CNT-04.

Milestone 0 runs a functionally empty FastAPI service on a single Uvicorn
worker (CNT-08). Real endpoints (health/chat/session) land in Milestone 5.
"""

import os

import uvicorn
from fastapi import FastAPI

app = FastAPI(title="AgentStrata", version="0.1.0")

# Container entrypoint: binding the wildcard interface is intentional here —
# the container network is the trust boundary (EXPOSE 8080 + host port mapping
# control external reachability), per the standard container-server pattern.
BIND_HOST = "0.0.0.0"


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": "agentstrata", "status": "bootstrap", "version": "0.1.0"}


def main() -> None:
    # Production entrypoint. CNT-08: exactly one worker; reload/debug mode is
    # prohibited in the production image — it is never enabled here regardless
    # of environment variables.
    uvicorn.run(
        "app.main:app",
        host=os.environ.get("AGENT_BIND_HOST", BIND_HOST),
        port=8080,
        workers=1,
        reload=False,
        access_log=True,
    )


if __name__ == "__main__":
    main()
