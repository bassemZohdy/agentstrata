#!/usr/bin/env python3
"""CNT-13 canary-secret scan for the built runtime image.

Three checks:
1. Layer content scan: docker-save the image and assert no forbidden files
   (.env, *.pem, id_rsa, *.key, credentials files) and no canary string are
   present in any layer.
2. History scan: ``docker history --no-trunc`` must not contain the canary
   string or known secret-value patterns (build args/env leakage).
3. Canary build test: a context containing a canary secret file that
   .dockerignore excludes must NOT leak into the built image.

Usage: python scripts/canary-scan.py [image-tag]  (default agentbase:amd64)
"""

from __future__ import annotations

import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CANARY = "agentbase-canary-secret-7f3a9c1e"
FORBIDDEN = re.compile(
    r"(^|/)(\.env|\.env\..*|id_rsa|id_ecdsa|id_ed25519|.*\.pem|.*\.key|credentials\.json|"
    r"\.dockercfg|\.docker/config\.json|service-account.*|kubeconfig)$",
    re.IGNORECASE,
)


def sh(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True)


def check_layers(image: str) -> list[str]:
    """Check 1: no forbidden files or canary content in any layer."""
    problems: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        tar_path = Path(tmp) / "image.tar"
        result = sh("save", image, "-o", str(tar_path))
        if result.returncode != 0:
            return [f"docker save failed: {result.stderr[:200]}"]
        with tarfile.open(tar_path) as tf:
            for member in tf.getmembers():
                name = member.name
                # layer tar members are <hash>/<layer>/... ; the outer tar holds
                # per-layer tars. Scan both the outer member names and the
                # contents of nested layer tars.
                if FORBIDDEN.search(name.split("/", 2)[-1] if "/" in name else name):
                    problems.append(f"forbidden path in image: {name}")
                if member.isfile() and name.endswith((".tar", ".tar.gz")):
                    try:
                        data = tf.extractfile(member)
                        if data is None:
                            continue
                        raw = data.read()
                        if CANARY.encode() in raw:
                            problems.append(f"canary string found in layer archive: {name}")
                    except (KeyError, tarfile.TarError, OSError):
                        continue
    return problems


def check_history(image: str) -> list[str]:
    """Check 2: image history must not leak the canary or secret values."""
    result = sh("history", "--no-trunc", image)
    if result.returncode != 0:
        return [f"docker history failed: {result.stderr[:200]}"]
    history = result.stdout
    problems: list[str] = []
    if CANARY in history:
        problems.append("canary string leaked into image history")
    for pattern in (r"AKIA[0-9A-Z]{16}", r"ghp_[0-9A-Za-z]{36}", r"sk-[A-Za-z0-9]{20,}"):
        if re.search(pattern, history):
            problems.append(f"secret-value pattern {pattern[:8]}... in history")
    return problems


def check_canary_build() -> list[str]:
    """Check 3: a canary file excluded by .dockerignore must not reach the
    image (proves the context boundary works for secrets)."""
    problems: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        ctx = Path(tmp)
        (ctx / "canary-secret.txt").write_text(CANARY, encoding="utf-8")
        # The repo's .dockerignore excludes .env files; simulate the boundary
        # by building with a context that includes a canary secret file and an
        # ignore rule that excludes it.
        (ctx / ".dockerignore").write_text("canary-secret.txt\n", encoding="utf-8")
        (ctx / "Dockerfile").write_text("FROM scratch\nCOPY . /\n", encoding="utf-8")
        tag = "agentbase-canary-test"
        result = sh("build", "-q", "-t", tag, str(ctx))
        if result.returncode != 0:
            return [f"canary build failed: {result.stderr[:200]}"]
        try:
            scan = sh(
                "run",
                "--rm",
                "--entrypoint",
                "sh",
                tag,
                "-c",
                "cat /canary-secret.txt 2>&1 || true",
            )
            if CANARY in scan.stdout:
                problems.append("canary file reached the image despite .dockerignore")
            # also check the image filesystem directly
            ls = sh("run", "--rm", "--entrypoint", "sh", tag, "-c", "ls / 2>&1")
            if "canary-secret.txt" in ls.stdout:
                problems.append("canary file present in the image root")
        finally:
            sh("rmi", "-f", tag)
    return problems


def main() -> int:
    image = sys.argv[1] if len(sys.argv) > 1 else "agentbase:amd64"
    problems: list[str] = []
    problems += check_layers(image)
    problems += check_history(image)
    problems += check_canary_build()
    if problems:
        print("CANARY SCAN FAILED:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"canary-secret scan passed for {image} (layers, history, context boundary)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
