"""Healthcheck module — fixed module path per REQUIREMENTS.md CNT-10/DEL-01.

Milestone 0: invocable stub. The real loopback probe (bound-port file check,
per CNT-10) is implemented in Milestone 8 alongside the Docker HEALTHCHECK.
"""


def main() -> None:
    # Milestone 8 implements the probe. For now, exiting 0 keeps the contract
    # that `python -m app.healthcheck` is a valid command (DEL-01).
    return


if __name__ == "__main__":
    main()
