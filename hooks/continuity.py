#!/usr/bin/env python3
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


SCHEMA = "v1"
MAX_HANDOFF_BYTES = 128 * 1024
MAX_GIT_STATUS_CHARS = 20_000
STALE_SECONDS = 7 * 24 * 60 * 60
SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def read_payload():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def project_dir(payload):
    raw = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd()
    return Path(raw).expanduser().resolve()


def continuity_dir():
    override = os.environ.get("REJECT_BENCH_CONTINUITY_DIR")
    root = Path(override) if override else Path(tempfile.gettempdir()) / "reject-bench-continuity"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    return root


def snapshot_path(payload, root):
    root = Path(root).resolve()
    session_id = str(payload.get("session_id") or "unknown")
    project_name = SAFE_NAME.sub("-", root.name).strip("-") or "project"
    identity = f"{root}\0{session_id}".encode("utf-8")
    suffix = hashlib.sha256(identity).hexdigest()[:16]
    return continuity_dir() / f"{project_name}-{suffix}.md"


def read_handoff(root):
    path = root / "HANDOFF.md"
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) > MAX_HANDOFF_BYTES:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def git_output(root, *args):
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            check=False,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return "unavailable"
    if result.returncode != 0:
        return "unavailable"
    return result.stdout.strip() or "(none)"


def prune_stale(directory):
    cutoff = datetime.now(timezone.utc).timestamp() - STALE_SECONDS
    for path in directory.glob("*.md"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue


def capture(payload):
    root = project_dir(payload)
    handoff = read_handoff(root)
    if handoff is None:
        print("continuity: HANDOFF.md를 읽지 못해 스냅샷을 만들지 않았습니다.", file=sys.stderr)
        return 0

    directory = continuity_dir()
    prune_stale(directory)
    target = snapshot_path(payload, root)
    trigger = payload.get("trigger") if payload.get("trigger") in {"auto", "manual"} else "unknown"
    captured_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    branch = git_output(root, "branch", "--show-current")
    head = git_output(root, "rev-parse", "HEAD")
    status_text = git_output(root, "status", "--short")[:MAX_GIT_STATUS_CHARS]

    snapshot = (
        "# 자동 compact 핸드오프\n\n"
        f"- schema: {SCHEMA}\n"
        f"- captured_at: {captured_at}\n"
        f"- trigger: {trigger}\n"
        f"- branch: {branch}\n"
        f"- head: {head}\n\n"
        "## 재개 규칙\n\n"
        "아래 HANDOFF.md를 먼저 읽고, compact 요약의 마지막 사용자 요청부터 계속한다. "
        "Git 체크포인트의 변경은 다른 작업자의 것일 수 있으므로 되돌리지 않는다.\n\n"
        "## compact 직전 Git 상태\n\n"
        "```text\n"
        f"{status_text}\n"
        "```\n\n"
        "## compact 직전 HANDOFF.md\n\n"
        f"{handoff.rstrip()}\n"
    )

    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=directory)
    try:
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(snapshot)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, target)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        print("continuity: 스냅샷 저장에 실패했습니다.", file=sys.stderr)
    return 0


def load(payload):
    root = project_dir(payload)
    source = payload.get("source")
    if source == "compact":
        target = snapshot_path(payload, root)
        try:
            snapshot = target.read_text(encoding="utf-8")
        except OSError:
            snapshot = None
        if snapshot:
            print("[compact 직후 자동 핸드오프 재주입]")
            print(snapshot)
            return 0

    handoff = read_handoff(root)
    if handoff:
        print("[HANDOFF.md 자동 주입]")
        print(handoff)
    return 0


def cleanup(payload):
    root = project_dir(payload)
    try:
        snapshot_path(payload, root).unlink(missing_ok=True)
    except OSError:
        pass
    return 0


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    payload = read_payload()
    if action == "capture":
        return capture(payload)
    if action == "load":
        return load(payload)
    if action == "cleanup":
        return cleanup(payload)
    print("usage: continuity.py capture|load|cleanup", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        raise SystemExit(0)
