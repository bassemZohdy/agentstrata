"""AgentConfig operator entry point (K8S-01).

Usage:
    python -m k8s_operator.main --namespace default [--resync-seconds 300]

In-cluster credentials (like the runtime watcher); RBAC in
k8s_operator/rbac.yaml.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="AgentConfig operator")
    parser.add_argument("--namespace", default="default")
    parser.add_argument("--resync-seconds", type=int, default=300)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if args.resync_seconds < 30:
        print("--resync-seconds must be >= 30", file=sys.stderr)
        return 2

    from .kube import RealKubeClient
    from .loop import run_operator

    try:
        asyncio.run(run_operator(RealKubeClient(), args.namespace, args.resync_seconds))
    except KeyboardInterrupt:
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
