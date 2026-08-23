#!/usr/bin/env python3
"""Activate one validated immutable Harness Ops distribution revision."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_ROOT = Path("/var/lib/darkexec/harness-ops")
DEFAULT_REMOTE = "https://github.com/DarkExec/harness-ops.git"
REVISION = __import__("re").compile(r"^[0-9a-f]{40}$")


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run(command: list[str], *, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        command, cwd=cwd, capture_output=True, text=True, check=False,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip() or "command failed"
        raise RuntimeError(detail)
    return completed.stdout.strip()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pass_bundle_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted((root / "passes").rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)


def immutable_tree(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            continue
        os.chmod(path, 0o555 if path.is_dir() else 0o444)
    os.chmod(root, 0o555)


def current_receipt(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def refresh(root: Path, remote: str) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o755)
    releases = root / "releases"
    releases.mkdir(exist_ok=True)
    lock_path = root / "refresh.lock"
    with lock_path.open("a+") as lock:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        repository = root / "repository.git"
        if not repository.exists():
            run(["git", "clone", "--quiet", "--mirror", remote, str(repository)])
        configured_remote = run(["git", f"--git-dir={repository}", "remote", "get-url", "origin"])
        if configured_remote != remote:
            raise RuntimeError("managed Harness Ops remote does not match the root-owned configuration")
        run([
            "git", f"--git-dir={repository}", "fetch", "--quiet", "--prune", "origin",
            "+refs/heads/main:refs/darkexec/candidate",
        ])
        revision = run([
            "git", f"--git-dir={repository}", "rev-parse", "refs/darkexec/candidate",
        ])
        if not REVISION.fullmatch(revision):
            raise RuntimeError("managed Harness Ops remote returned an invalid revision")
        receipt_path = root / "current.json"
        prior = current_receipt(receipt_path)
        prior_revision = prior.get("revision")
        if isinstance(prior_revision, str) and REVISION.fullmatch(prior_revision):
            ancestor = subprocess.run([
                "git", f"--git-dir={repository}", "merge-base", "--is-ancestor",
                prior_revision, revision,
            ], check=False)
            if ancestor.returncode:
                raise RuntimeError("managed Harness Ops revision is not a fast-forward")
        release = releases / revision
        if not release.exists():
            candidate = Path(tempfile.mkdtemp(prefix=".candidate-", dir=releases))
            try:
                run(["git", "clone", "--quiet", str(repository), str(candidate)])
                run(["git", "checkout", "--quiet", "--detach", revision], cwd=candidate)
                run([str(candidate / "scripts/validate.sh")], cwd=candidate)
                shutil.rmtree(candidate / ".git")
                immutable_tree(candidate)
                os.replace(candidate, release)
            finally:
                if candidate.exists():
                    shutil.rmtree(candidate, ignore_errors=True)
        doctrine_checksum = sha256(release / "harness-ops.md")
        bundle_checksum = pass_bundle_sha256(release)
        if prior_revision == revision:
            if (
                prior.get("harnessOpsSha256") != doctrine_checksum
                or prior.get("passBundleSha256") != bundle_checksum
            ):
                raise RuntimeError("managed Harness Ops current receipt does not match its immutable release")
            status = "current"
        else:
            temporary_link = root / f".current.{os.getpid()}"
            temporary_link.symlink_to(Path("releases") / revision)
            os.replace(temporary_link, root / "current")
            status = "updated"
        history = [revision] + [
            item for item in prior.get("history", [])
            if isinstance(item, str) and REVISION.fullmatch(item) and item != revision
        ]
        history = history[:3]
        receipt = {
            "schemaVersion": 1,
            "status": status,
            "revision": revision,
            "harnessOpsSha256": doctrine_checksum,
            "passBundleSha256": bundle_checksum,
            "activatedAt": now(),
            "history": history,
        }
        atomic_json(receipt_path, receipt)
        for path in releases.iterdir():
            if path.is_dir() and REVISION.fullmatch(path.name) and path.name not in history:
                os.chmod(path, 0o755)
                for child in path.rglob("*"):
                    if not child.is_symlink():
                        os.chmod(child, 0o755 if child.is_dir() else 0o644)
                shutil.rmtree(path)
        return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    root = Path(os.environ.get("DARKEXEC_HARNESS_OPS_ROOT", str(DEFAULT_ROOT)))
    remote = os.environ.get("DARKEXEC_HARNESS_OPS_REMOTE", DEFAULT_REMOTE)
    try:
        receipt = refresh(root, remote)
    except (OSError, RuntimeError, json.JSONDecodeError) as exc:
        if args.json:
            print(json.dumps({"status": "failed", "error": str(exc)[:1000]}))
        else:
            print(f"Harness Ops refresh failed: {exc}", file=__import__("sys").stderr)
        return 1
    print(json.dumps(receipt) if args.json else (
        f"harness-ops {receipt['status']}: revision={receipt['revision']} "
        f"sha256={receipt['harnessOpsSha256']}"
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
