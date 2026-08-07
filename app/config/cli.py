"""CLI surface (REQUIREMENTS.md CFG-10, CFG-10a, CFG-11, CFG-11a).

Entrypoint accepts ``--<dotted.path>=<value>`` for any bindable schema path
plus the bootstrap flags ``--profile``, ``--config-dir``, ``--print-env``,
``--dump-config``, ``--validate``, ``--version``, and ``--help``. Exit
codes: 0 ok, 64 EX_USAGE (unknown paths / positional args / malformed
flags), 78 EX_CONFIG (configuration failure). ``--validate``,
``--dump-config``, and ``--print-env`` are mutually exclusive.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence

from . import capabilities, dump
from . import mode as mode_mod
from .models import SCHEMA_MAJOR, SCHEMA_VERSION
from .resolver import DEFAULT_BUNDLED_DIR, ConfigError, UsageError, resolve
from .validate import validate_resolution

EX_OK = 0
EX_USAGE = 64
EX_CONFIG = 78

BOOTSTRAP_FLAGS = (
    "--profile <name>",
    "--config-dir <absolute-path>",
    "--validate",
    "--dump-config",
    "--print-env",
    "--version",
    "--help",
)

HELP_TEXT = """Agentbase runtime configuration CLI

Usage: python -m app.main [options] [--<dotted.path>=<value> ...]

Bootstrap flags:
  --profile <name>            Select the active profile (tiers 2 and 4)
  --config-dir <path>         Absolute config directory (tier 3/4, default /etc/agent)
  --validate                  Resolve + validate tiers 1-7, print OK, exit without starting
  --dump-config               Resolve + validate, print canonical masked YAML, exit
  --print-env                 Print the schema-derived AGENT_* catalog (CFG-17) and exit
  --version                   Print runtime version and exit without loading config
  --help                      Show this help and exit

Dotted-path flags:
  --<dotted.path>=<value>     Bind any schema leaf (scalars) or JSON value
                              (models/lists/passthrough maps); last occurrence wins

Exit codes:
  0  success
  64 usage error (unknown path, positional arguments, malformed flags)
  78 configuration error (aggregate report on stderr)
"""


def _bootstrap(argv: list[str]) -> tuple[str | None, str | None, str | None, str | None]:
    """Extract --profile/--config-dir/--validate/--dump-config/--print-env/
    --version/--help.

    Returns (profile, config_dir, action, help_flag). Dotted-path and
    positional arguments are left untouched for tier-7 parsing.
    ``--validate``, ``--dump-config``, and ``--print-env`` are mutually
    exclusive (CFG-10a/10b).
    """
    profile: str | None = None
    config_dir: str | None = None
    action: str | None = None
    help_flag: str | None = None
    has_validate = False
    has_dump = False
    has_print_env = False
    for arg in argv:
        if arg == "--validate":
            action = "validate"
            has_validate = True
        elif arg == "--dump-config":
            action = "dump"
            has_dump = True
        elif arg == "--print-env":
            action = "print-env"
            has_print_env = True
        elif arg == "--version":
            action = "version"
        elif arg == "--help" or arg == "-h":
            help_flag = "help"
        elif arg.startswith("--profile="):
            profile = arg.split("=", 1)[1]
        elif arg == "--profile":
            profile = ""
            # value consumed below
        elif arg.startswith("--config-dir="):
            config_dir = arg.split("=", 1)[1]
        elif arg == "--config-dir":
            config_dir = ""
    # consume separated values for --profile/--config-dir
    for i, arg in enumerate(argv):
        if arg == "--profile" and i + 1 < len(argv):
            profile = argv[i + 1]
        if arg == "--config-dir" and i + 1 < len(argv):
            config_dir = argv[i + 1]
    if sum((has_validate, has_dump, has_print_env)) > 1:
        raise UsageError("--validate, --dump-config and --print-env are mutually exclusive")
    return profile, config_dir, action, help_flag


def _check_usage(argv: list[str]) -> None:
    """Reject positional arguments and malformed flags (CFG-10 -> exit 64)."""
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in (
            "--validate",
            "--dump-config",
            "--print-env",
            "--version",
            "--help",
            "-h",
        ):
            i += 1
            continue
        if arg in ("--profile", "--config-dir"):
            if i + 1 >= len(argv):
                raise UsageError(f"{arg} requires a value")
            i += 2  # consume the flag and its value
            continue
        if arg.startswith("--profile=") or arg.startswith("--config-dir="):
            i += 1
            continue
        if arg.startswith("--") and "=" in arg:
            path = arg[2:].split("=", 1)[0]
            if path in (
                "profile",
                "config-dir",
                "validate",
                "dump-config",
                "print-env",
                "version",
                "help",
            ):
                raise UsageError(f"flag {path!r} does not take a value")
            i += 1
            continue  # dotted paths validated by the resolver
        if arg.startswith("--"):
            raise UsageError(f"malformed flag: {arg!r}")
        raise UsageError(f"unexpected positional argument: {arg!r}")


def _version_string() -> str:
    import app

    return (
        f"agentbase {app.__version__}\n"
        f"commit {_build_commit()}\n"
        f"schema major {SCHEMA_MAJOR} (version {SCHEMA_VERSION})\n"
        f"phase {capabilities.PHASE}"
    )


def _build_commit() -> str:
    try:
        import subprocess

        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:  # noqa: BLE001
        pass
    return "unknown"


def _print_report(issues) -> None:
    for issue in issues:
        tier = f" (tier {issue.tier})" if issue.tier else ""
        print(f"{issue.path}: {issue.code}: {issue.message}{tier}", file=sys.stderr)


def run(
    argv: Sequence[str] | None = None,
    *,
    bundled_dir: str | None = None,
) -> int:
    """CLI entrypoint. Returns the process exit code (0/64/78).

    ``bundled_dir`` is the tier-1/2 directory: ``/app/config`` in the image.
    ``AGENT_BUNDLED_DIR`` overrides it for local development and tests (this
    is a dev-only escape hatch, not a schema field — the image always uses
    ``/app/config`` per CFG-01).
    """
    argv = list(argv) if argv is not None else sys.argv[1:]
    if bundled_dir is None:
        bundled_dir = os.environ.get("AGENT_BUNDLED_DIR") or DEFAULT_BUNDLED_DIR

    try:
        _check_usage(argv)
    except UsageError as exc:
        print(f"usage error: {exc}", file=sys.stderr)
        print(HELP_TEXT, file=sys.stderr)
        return EX_USAGE

    try:
        profile, config_dir, action, help_flag = _bootstrap(argv)
    except UsageError as exc:
        print(f"usage error: {exc}", file=sys.stderr)
        return EX_USAGE

    if help_flag:
        print(HELP_TEXT)
        return EX_OK
    if action == "version":
        print(_version_string())
        return EX_OK
    # CFG-10b: the catalog is schema-derived — no config resolution, so a
    # broken deployment config cannot hide it.
    if action == "print-env":
        from .env_catalog import render_catalog

        sys.stdout.write(render_catalog())
        return EX_OK

    try:
        res = resolve(
            argv=argv,
            cli_profile=profile,
            cli_config_dir=config_dir,
            bundled_dir=bundled_dir,
        )
        result = validate_resolution(res)
    except UsageError as exc:
        print(f"usage error: {exc}", file=sys.stderr)
        return EX_USAGE
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return EX_CONFIG
    except Exception as exc:  # noqa: BLE001
        print(f"configuration error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EX_CONFIG

    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    # CFG-18 (E1-6): one boot summary line after the individual CFG-08
    # warnings so unmatched-variable volume is visible at a glance.
    unmatched = sum(
        1
        for w in result.warnings
        if w.startswith("environment variable AGENT_") and "matches no schema path" in w
    )
    if unmatched:
        print(
            f"warning: {unmatched} unmatched AGENT_* variable(s) ignored (CFG-08)",
            file=sys.stderr,
        )

    if action == "validate":
        if result.ok:
            print("OK")
            return EX_OK
        _print_report(result.issues)
        return EX_CONFIG

    if action == "dump":
        if not result.ok:
            _print_report(result.issues)
            return EX_CONFIG
        if result.config is None:
            raise RuntimeError("validated config unexpectedly None")
        sys.stdout.write(dump.dump_config(res, result.config))
        return EX_OK

    # Default boot path: full validation; a failure exits 78 before bind
    # (CFG-15 leaves server startup to the runtime main).
    if not result.ok:
        _print_report(result.issues)
        return EX_CONFIG

    # MODE-01..04 selection is part of boot; expose it here for the runtime.
    if result.config is None:
        raise RuntimeError("validated config unexpectedly None")
    # LLM-04 (E1-5): opt-in credential-variable inference is fail-closed —
    # an inferred-but-absent variable is a boot error naming the variable.
    from .validate import auto_api_key_error

    api_key_error = auto_api_key_error(result.config, dict(__import__("os").environ))
    if api_key_error is not None:
        print(f"configuration error: {api_key_error}", file=sys.stderr)
        return EX_CONFIG
    try:
        selected_mode, mode_warnings = mode_mod.select_mode(
            result.config, dict(__import__("os").environ)
        )
        for warning in mode_warnings:
            print(f"warning: {warning}", file=sys.stderr)
        print(f"mode: {selected_mode}", file=sys.stderr)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return EX_CONFIG
    return EX_OK


if __name__ == "__main__":
    sys.exit(run())
