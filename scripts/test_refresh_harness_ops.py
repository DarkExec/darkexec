#!/usr/bin/env python3
"""Exercise immutable Harness Ops activation and fast-forward rejection."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], cwd: Path, *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=check)


def write_fixture(source: Path, doctrine: str) -> None:
    (source / "harness-ops.md").write_text(doctrine)
    (source / "passes" / "harness").mkdir(parents=True, exist_ok=True)
    (source / "passes" / "harness" / "AGENTS.md").write_text("# Harness\n")
    (source / "scripts").mkdir(exist_ok=True)
    validate = source / "scripts" / "validate.sh"
    validate.write_text("#!/usr/bin/env bash\nset -euo pipefail\ntest -s harness-ops.md\n")
    validate.chmod(0o755)


def commit(source: Path, message: str, *, amend: bool = False) -> str:
    run(["git", "add", "."], source)
    command = [
        "git", "-c", "user.name=Harness Test", "-c", "user.email=test@darkexec.invalid",
        "commit", "--quiet", "-m", message,
    ]
    if amend:
        command.extend(["--amend", "--no-edit"])
    run(command, source)
    return run(["git", "rev-parse", "HEAD"], source).stdout.strip()


def refresh(cache: Path, source: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(ROOT / "scripts/refresh_harness_ops.py"), "--json"],
        capture_output=True, text=True, check=False,
        env={
            **os.environ,
            "DARKEXEC_HARNESS_OPS_ROOT": str(cache),
            "DARKEXEC_HARNESS_OPS_REMOTE": str(source),
        },
    )


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source, cache = root / "source", root / "cache"
        source.mkdir()
        run(["git", "init", "--quiet", "--initial-branch=main"], source)
        write_fixture(source, "# Doctrine one\n")
        first = commit(source, "first")
        activated = refresh(cache, source)
        receipt = json.loads(activated.stdout)
        assert activated.returncode == 0 and receipt["status"] == "updated", receipt
        assert receipt["revision"] == first
        assert (cache / "current" / "harness-ops.md").read_text() == "# Doctrine one\n"
        assert (cache / "releases" / first).stat().st_mode & 0o222 == 0
        current = json.loads(refresh(cache, source).stdout)
        assert current["status"] == "current" and current["revision"] == first, current
        (source / "harness-ops.md").write_text("# Doctrine two\n")
        second = commit(source, "second")
        advanced = json.loads(refresh(cache, source).stdout)
        assert advanced["status"] == "updated" and advanced["revision"] == second, advanced
        assert advanced["history"] == [second, first], advanced
        (source / "harness-ops.md").write_text("# Rewritten doctrine\n")
        rewritten = commit(source, "rewrite", amend=True)
        assert rewritten != second
        rejected = refresh(cache, source)
        assert rejected.returncode != 0 and "not a fast-forward" in rejected.stdout, rejected.stdout
        assert (cache / "current").resolve().name == second
    print(json.dumps({"status": "passed", "contract": "immutable-harness-ops-distribution"}))


if __name__ == "__main__":
    main()
