"""래퍼 E2E — 시험용 mock 가드로 실제 2종 인터페이스를 재현한다.

실제 가드 스크립트는 실행하지 않는다. mock 가드만 임시 디렉터리에 만들고,
(1) stderr + exit 2 (전역 block-dangerous-git 인터페이스)
(2) permissionDecision=deny JSON + exit 0 (reply-gate 인터페이스)
를 재현해 stdin pass-through·투명 전달·기록을 검증한다.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from rejectbench import AppendStore, GuardEvent, GuardRegistry, Origin, enforcement_ref_for

REPO_ROOT = Path(__file__).resolve().parents[1]

STDERR_GUARD = """#!/bin/bash
INPUT=$(cat)
if printf '%s' "$INPUT" | grep -q "push --force"; then
  echo "BLOCKED: command matches dangerous pattern 'push --force'. The user has prevented you from doing this." >&2
  exit 2
fi
exit 0
"""

DENY_JSON_GUARD = """#!/bin/bash
INPUT=$(cat)
if printf '%s' "$INPUT" | grep -q "reports/evaluation-live"; then
  printf '%s\\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"커밋된 라이브 실측 리포트는 사후 편집하지 않는다"}}'
  exit 0
fi
exit 0
"""

SECRET_ECHO_GUARD = """#!/bin/bash
cat > /dev/null
echo "BLOCKED: OPENAI_API_KEY=sk-live-abcdef0123456789 must never be committed" >&2
exit 2
"""

ARG_SENSITIVE_GUARD = """#!/bin/bash
cat > /dev/null
if [ "${1:-}" = "strict" ]; then
  echo "BLOCKED by strict mode" >&2
  exit 2
fi
exit 0
"""


def write_guard(tmp_path: Path, body: str, name: str = "mock-guard.sh") -> Path:
    script = tmp_path / name
    script.write_text(body, encoding="utf-8")
    script.chmod(0o755)
    return script


def payload(
    command: str = "git push --force origin main",
    tool_name: str = "Bash",
    tool_input: dict | None = None,
    session_id: str | None = "s-e2e",
) -> str:
    body: dict = {
        "hook_event_name": "PreToolUse",
        "cwd": "/Users/ian/workspace/reject-bench",
        "tool_name": tool_name,
        "tool_input": {"command": command} if tool_input is None else tool_input,
    }
    if session_id is not None:
        body["session_id"] = session_id
    return json.dumps(body, ensure_ascii=False)


def run_wrapper(
    guard: Path | str,
    stdin_text: str,
    store_root: Path,
    fallback_dir: Path,
    *,
    extra_env: dict[str, str] | None = None,
    extra_args: list[str] | None = None,
) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("REJECTBENCH_")}
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["REJECTBENCH_STORE"] = str(store_root)
    env["REJECTBENCH_FALLBACK_DIR"] = str(fallback_dir)
    if extra_env:
        env.update(extra_env)
    argv = [sys.executable, "-m", "rejectbench.wrapper", "--guard", str(guard)]
    if extra_args:
        argv += extra_args
    return subprocess.run(
        argv,
        input=stdin_text.encode("utf-8"),
        capture_output=True,
        env=env,
        cwd=str(REPO_ROOT),
        timeout=60,
    )


@pytest.fixture
def paths(tmp_path):
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    return tmp_path / "store", fallback


def stored_events(store_root: Path) -> list[GuardEvent]:
    return [r for r in AppendStore(store_root).load().records if isinstance(r, GuardEvent)]


# --- 투명 전달 ---------------------------------------------------------------


def test_blocked_stderr_guard_passthrough_and_record(tmp_path, paths):
    store_root, fallback = paths
    guard = write_guard(tmp_path, STDERR_GUARD)
    proc = run_wrapper(guard, payload(), store_root, fallback)
    assert proc.returncode == 2
    assert proc.stdout == b""
    assert (
        proc.stderr.decode("utf-8").strip()
        == "BLOCKED: command matches dangerous pattern 'push --force'. The user has prevented you from doing this."
    )
    (event,) = stored_events(store_root)
    assert event.session_id == "claude:s-e2e"
    assert event.action.command_verb == "git"
    assert "push --force" in event.reason


def test_pass_result_is_transparent_and_unrecorded(tmp_path, paths):
    store_root, fallback = paths
    guard = write_guard(tmp_path, STDERR_GUARD)
    proc = run_wrapper(guard, payload(command="git status"), store_root, fallback)
    assert proc.returncode == 0
    assert proc.stdout == b""
    assert proc.stderr == b""
    assert stored_events(store_root) == []
    assert not (store_root / "records.jsonl").exists()


def test_deny_json_guard_passthrough_and_record(tmp_path, paths):
    store_root, fallback = paths
    guard = write_guard(tmp_path, DENY_JSON_GUARD)
    stdin_text = payload(
        tool_name="Edit",
        tool_input={"file_path": "reports/evaluation-live.md", "old_string": "x"},
    )
    proc = run_wrapper(guard, stdin_text, store_root, fallback)
    assert proc.returncode == 0
    assert proc.stderr == b""
    emitted = json.loads(proc.stdout.decode("utf-8"))
    assert emitted["hookSpecificOutput"]["permissionDecision"] == "deny"
    (event,) = stored_events(store_root)
    assert event.reason == "커밋된 라이브 실측 리포트는 사후 편집하지 않는다"
    assert event.action.tool_name == "Edit"
    assert event.action.target_path == "reports/evaluation-live.md"


def test_guard_args_are_forwarded(tmp_path, paths):
    store_root, fallback = paths
    guard = write_guard(tmp_path, ARG_SENSITIVE_GUARD)
    proc = run_wrapper(guard, payload(), store_root, fallback, extra_args=["strict"])
    assert proc.returncode == 2
    assert b"BLOCKED by strict mode" in proc.stderr
    proc = run_wrapper(guard, payload(), store_root, fallback)
    assert proc.returncode == 0


# --- 출처·비밀 제거 (E2E) ----------------------------------------------------


def test_test_session_env_marks_test_origin(tmp_path, paths):
    store_root, fallback = paths
    guard = write_guard(tmp_path, STDERR_GUARD)
    proc = run_wrapper(
        guard,
        payload(),
        store_root,
        fallback,
        extra_env={"REJECTBENCH_TEST_SESSION": "1"},
    )
    assert proc.returncode == 2
    (event,) = stored_events(store_root)
    assert event.origin is Origin.TEST


def test_secret_in_guard_stderr_is_scrubbed_in_store_but_passed_through(tmp_path, paths):
    store_root, fallback = paths
    guard = write_guard(tmp_path, SECRET_ECHO_GUARD)
    proc = run_wrapper(guard, payload(command="git commit"), store_root, fallback)
    assert proc.returncode == 2
    # 투명 전달: 가드의 원래 stderr는 그대로 (기록기가 바꾸지 않는다)
    assert b"sk-live-abcdef0123456789" in proc.stderr
    # 저장소에는 비밀 평문이 없다
    raw = (store_root / "records.jsonl").read_text(encoding="utf-8")
    assert "sk-live-abcdef0123456789" not in raw
    (event,) = stored_events(store_root)
    assert "[REDACTED]" in event.reason


# --- 등록·drift (E2E) --------------------------------------------------------


def test_registered_guard_reference_and_drift(tmp_path, paths):
    store_root, fallback = paths
    guard = write_guard(tmp_path, STDERR_GUARD)
    registry = GuardRegistry(AppendStore(store_root))
    spec = registry.register(
        guard_id="guard-git",
        project="global",
        purpose="위험 git 명령 차단",
        policy="force push를 차단한다",
        allow_examples=("git status",),
        block_examples=("git push --force",),
        enforcement_ref=enforcement_ref_for(guard),
    ).spec

    proc = run_wrapper(guard, payload(), store_root, fallback)
    assert proc.returncode == 2
    events = stored_events(store_root)
    assert events[-1].guard_id == "guard-git"
    assert events[-1].guard_version == spec.version
    assert events[-1].drift is False

    write_guard(tmp_path, STDERR_GUARD + "# drifted\n")
    proc = run_wrapper(guard, payload(), store_root, fallback)
    assert proc.returncode == 2
    events = stored_events(store_root)
    assert events[-1].drift is True


# --- 비블로킹 (E2E): 주 저장을 인위적으로 막는다 ------------------------------


def test_record_failure_never_changes_guard_result(tmp_path, paths):
    _, fallback = paths
    guard = write_guard(tmp_path, STDERR_GUARD)
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    broken_store = blocker / "store"  # mkdir가 반드시 실패한다

    proc = run_wrapper(guard, payload(), broken_store, fallback)
    # 가드의 원래 결과 불변
    assert proc.returncode == 2
    assert (
        proc.stderr.decode("utf-8").strip()
        == "BLOCKED: command matches dangerous pattern 'push --force'. The user has prevented you from doing this."
    )
    assert proc.stdout == b""
    # 대체 매체 최소 흔적
    trace_file = fallback / "rejectbench-loss-fallback.log"
    assert trace_file.exists()
    line = json.loads(trace_file.read_text(encoding="utf-8").splitlines()[0])
    assert line["kind"] == "write_failure"


def test_missing_guard_script_fails_open_with_visible_error(tmp_path, paths):
    store_root, fallback = paths
    proc = run_wrapper(tmp_path / "no-such-guard.sh", payload(), store_root, fallback)
    assert proc.returncode == 1  # exit 2(차단)가 아니다 — 보이되 막지 않는다
    assert b"rejectbench.wrapper" in proc.stderr
    assert stored_events(store_root) == []


# --- 동시 실행 (E2E) ---------------------------------------------------------


def test_concurrent_wrappers_append_intact_lines(tmp_path, paths):
    store_root, fallback = paths
    guard = write_guard(tmp_path, STDERR_GUARD)

    def run_one(i: int):
        return run_wrapper(guard, payload(session_id=f"s-{i}"), store_root, fallback)

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        procs = list(pool.map(run_one, range(6)))
    assert all(p.returncode == 2 for p in procs)
    result = AppendStore(store_root).load()
    assert not result.corrupt
    events = [r for r in result.records if isinstance(r, GuardEvent)]
    assert len(events) == 6
    assert {e.session_id for e in events} == {f"claude:s-{i}" for i in range(6)}
