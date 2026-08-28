"""기록기 (spec §3.2 규칙, §3.6, §5 "발동 시") — 조립·출처·비블로킹 계약."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from rejectbench import (
    AppendStore,
    CaptureStatus,
    GuardEvent,
    GuardRegistry,
    LossKind,
    LossRecord,
    Origin,
    OriginEvidence,
    enforcement_ref_for,
    production_root,
)
from rejectbench.recorder import (
    FALLBACK_DIR_ENV,
    FALLBACK_FILENAME,
    STORE_ENV,
    TEST_SESSION_ENV,
    GuardResult,
    interpret_guard_result,
    record_guard_result,
    resolve_store_root,
)

NOW = datetime(2026, 8, 29, 9, 0, 0, tzinfo=timezone.utc)

BLOCKED_STDERR = GuardResult(
    exit_code=2,
    stdout="",
    stderr="BLOCKED: command matches dangerous pattern 'push --force'.",
)
PASSED = GuardResult(exit_code=0, stdout="", stderr="")
DENY_JSON = GuardResult(
    exit_code=0,
    stdout=json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "커밋된 라이브 실측 리포트는 사후 편집하지 않는다",
            }
        },
        ensure_ascii=False,
    ),
    stderr="",
)


def payload_text(
    command: str = "git push --force origin main",
    session_id: str | None = "s-123",
    cwd: str = "/Users/ian/workspace/reject-bench",
    tool_name: str = "Bash",
    tool_input: dict | None = None,
) -> str:
    payload: dict = {
        "hook_event_name": "PreToolUse",
        "cwd": cwd,
        "tool_name": tool_name,
        "tool_input": {"command": command} if tool_input is None else tool_input,
    }
    if session_id is not None:
        payload["session_id"] = session_id
    return json.dumps(payload, ensure_ascii=False)


def record(tmp_path: Path, **kwargs):
    store = kwargs.pop("store", None) or AppendStore(tmp_path / "store")
    defaults = dict(
        payload_text=payload_text(),
        guard_path="/fake/guards/block-dangerous-git.sh",
        result=BLOCKED_STDERR,
        env={},
        store=store,
        now=NOW,
    )
    defaults.update(kwargs)
    return store, record_guard_result(**defaults)


def loaded_events(store: AppendStore) -> list[GuardEvent]:
    return [r for r in store.load().records if isinstance(r, GuardEvent)]


# --- 차단 판정 ---------------------------------------------------------------


def test_exit_2_with_stderr_is_blocked():
    judgement = interpret_guard_result(BLOCKED_STDERR)
    assert judgement.blocked
    assert "push --force" in judgement.reason


def test_deny_json_is_blocked_with_json_reason():
    judgement = interpret_guard_result(DENY_JSON)
    assert judgement.blocked
    assert judgement.reason == "커밋된 라이브 실측 리포트는 사후 편집하지 않는다"


def test_exit_0_without_deny_is_not_blocked():
    assert not interpret_guard_result(PASSED).blocked
    assert not interpret_guard_result(
        GuardResult(exit_code=0, stdout='{"other": true}', stderr="")
    ).blocked


def test_exit_1_is_not_a_block():
    result = GuardResult(exit_code=1, stdout="", stderr="some hook error")
    assert not interpret_guard_result(result).blocked


# --- 차단 사건 조립 ----------------------------------------------------------


def test_pass_result_records_nothing(tmp_path):
    store, outcome = record(tmp_path, result=PASSED)
    assert not outcome.blocked and not outcome.recorded
    assert not store.path.exists()


def test_blocked_event_assembly(tmp_path):
    store, outcome = record(tmp_path)
    assert outcome.blocked and outcome.recorded
    (event,) = loaded_events(store)
    assert event.session_id == "claude:s-123"
    assert event.project == "reject-bench"
    assert event.occurred_at == NOW
    assert event.action.tool_name == "Bash"
    assert event.action.command_verb == "git"
    assert event.action.target_path is None
    assert event.action.heredoc is False
    assert "push --force" in event.reason
    assert event.origin is Origin.OPERATION
    assert event.origin_evidence is OriginEvidence.DEFAULT_INHERITED
    assert event.capture_status is CaptureStatus.COMPLETE
    assert event.unregistered is True  # 빈 등록부


def test_action_summary_never_holds_full_command(tmp_path):
    command = "git commit -m 'wip' && git push --force origin main"
    store, _ = record(tmp_path, payload_text=payload_text(command=command))
    (event,) = loaded_events(store)
    raw = store.path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert payload["action"] == {
        "tool_name": "Bash",
        "command_verb": "git",
        "target_path": None,
        "heredoc": False,
    }
    assert command not in json.dumps(payload["action"], ensure_ascii=False)


def test_heredoc_and_target_path_extraction(tmp_path):
    command = "tee reports/evaluation-live.md <<'EOF'\nhello\nEOF"
    store, _ = record(tmp_path, payload_text=payload_text(command=command))
    (event,) = loaded_events(store)
    assert event.action.command_verb == "tee"
    assert event.action.target_path == "reports/evaluation-live.md"
    assert event.action.heredoc is True


def test_edit_tool_target_path(tmp_path):
    store, _ = record(
        tmp_path,
        payload_text=payload_text(
            tool_name="Edit",
            tool_input={"file_path": "reports/evaluation-live.md", "old_string": "a"},
        ),
        result=DENY_JSON,
    )
    (event,) = loaded_events(store)
    assert event.action.tool_name == "Edit"
    assert event.action.command_verb is None
    assert event.action.target_path == "reports/evaluation-live.md"


def test_reason_is_scrubbed_at_ingest(tmp_path):
    dirty = GuardResult(
        exit_code=2,
        stdout="",
        stderr="BLOCKED: OPENAI_API_KEY=sk-live-abcdef0123456789 must not be pushed",
    )
    store, _ = record(tmp_path, result=dirty)
    (event,) = loaded_events(store)
    assert "sk-live-abcdef0123456789" not in event.reason
    assert "OPENAI_API_KEY=" in event.reason
    assert "sk-live-abcdef0123456789" not in store.path.read_text(encoding="utf-8")


# --- 출처 결정표 -------------------------------------------------------------


def test_test_flag_env_marks_origin_test(tmp_path):
    store, _ = record(tmp_path, env={TEST_SESSION_ENV: "1"})
    (event,) = loaded_events(store)
    assert event.origin is Origin.TEST
    assert event.origin_evidence is OriginEvidence.EXPLICIT_FLAG


def test_test_flag_presence_counts_even_when_empty(tmp_path):
    store, _ = record(tmp_path, env={TEST_SESSION_ENV: ""})
    (event,) = loaded_events(store)
    assert event.origin is Origin.TEST


def test_missing_session_id_means_unknown_and_partial(tmp_path):
    store, _ = record(tmp_path, payload_text=payload_text(session_id=None))
    (event,) = loaded_events(store)
    assert event.origin is Origin.UNKNOWN
    assert event.origin_evidence is OriginEvidence.NO_CONTEXT
    assert event.capture_status is CaptureStatus.PARTIAL


def test_unparseable_payload_means_unknown_but_still_recorded(tmp_path):
    store, outcome = record(tmp_path, payload_text="this is not json{{{")
    assert outcome.recorded
    (event,) = loaded_events(store)
    assert event.origin is Origin.UNKNOWN
    assert event.capture_status is CaptureStatus.PARTIAL
    assert event.action.tool_name == "unknown"


def test_no_context_beats_test_flag(tmp_path):
    store, _ = record(
        tmp_path,
        payload_text=payload_text(session_id=None),
        env={TEST_SESSION_ENV: "1"},
    )
    (event,) = loaded_events(store)
    assert event.origin is Origin.UNKNOWN
    assert event.origin_evidence is OriginEvidence.NO_CONTEXT


# --- 등록부 연계: 등록·미등록·drift --------------------------------------------


def make_guard_script(tmp_path: Path, body: str = "#!/bin/bash\nexit 0\n") -> Path:
    script = tmp_path / "guards" / "mock-guard.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(body, encoding="utf-8")
    script.chmod(0o755)
    return script


def register_guard(store: AppendStore, script: Path):
    registry = GuardRegistry(store)
    return registry.register(
        guard_id="guard-git",
        project="global",
        purpose="위험 git 명령 차단",
        policy="force push를 차단한다",
        allow_examples=("git status",),
        block_examples=("git push --force",),
        enforcement_ref=enforcement_ref_for(script),
    ).spec


def test_registered_guard_is_referenced_without_drift(tmp_path):
    store = AppendStore(tmp_path / "store")
    script = make_guard_script(tmp_path)
    spec = register_guard(store, script)
    store, _ = record(tmp_path, store=store, guard_path=str(script))
    (event,) = loaded_events(store)
    assert event.unregistered is False
    assert event.guard_id == "guard-git"
    assert event.guard_version == spec.version
    assert event.guard_spec_hash == spec.content_hash
    assert event.drift is False


def test_modified_script_marks_drift(tmp_path):
    store = AppendStore(tmp_path / "store")
    script = make_guard_script(tmp_path)
    register_guard(store, script)
    script.write_text("#!/bin/bash\n# changed\nexit 0\n", encoding="utf-8")
    store, _ = record(tmp_path, store=store, guard_path=str(script))
    (event,) = loaded_events(store)
    assert event.unregistered is False
    assert event.drift is True


def test_unknown_script_is_recorded_unregistered_with_hint(tmp_path):
    store, _ = record(tmp_path, guard_path="/somewhere/else/custom-guard.sh")
    (event,) = loaded_events(store)
    assert event.unregistered is True
    assert event.guard_id is None
    assert event.guard_hint == "/somewhere/else/custom-guard.sh"


# --- 비블로킹: 기록 실패가 가드 결과를 바꾸지 않는다 --------------------------


class FailsOnEvent:
    """GuardEvent append만 실패하는 store 대역 — LossRecord는 받는다."""

    def __init__(self, inner: AppendStore):
        self.inner = inner

    def append(self, record):
        if isinstance(record, GuardEvent):
            raise OSError("disk full")
        self.inner.append(record)

    def load(self):
        return self.inner.load()


class AlwaysFails:
    def append(self, record):
        raise OSError("disk full")

    def load(self):
        raise OSError("disk full")


def test_primary_failure_writes_loss_record(tmp_path):
    inner = AppendStore(tmp_path / "store")
    store, outcome = record(tmp_path, store=FailsOnEvent(inner))
    assert outcome.blocked
    assert not outcome.recorded
    assert outcome.loss_recorded
    losses = [r for r in inner.load().records if isinstance(r, LossRecord)]
    assert len(losses) == 1
    assert losses[0].kind is LossKind.WRITE_FAILURE
    assert "OSError" in losses[0].detail


def test_total_failure_leaves_fallback_trace_and_never_raises(tmp_path):
    fallback_dir = tmp_path / "fallback"
    fallback_dir.mkdir()
    _, outcome = record(
        tmp_path,
        store=AlwaysFails(),
        env={FALLBACK_DIR_ENV: str(fallback_dir)},
    )
    assert outcome.blocked
    assert not outcome.recorded
    assert not outcome.loss_recorded
    assert outcome.fallback_used
    trace = (fallback_dir / FALLBACK_FILENAME).read_text(encoding="utf-8")
    line = json.loads(trace.splitlines()[0])
    assert line["kind"] == "write_failure"


def test_loss_detail_never_contains_payload_text(tmp_path):
    secret_command = "git push --force # sk-live-abcdef0123456789"
    inner = AppendStore(tmp_path / "store")
    record(
        tmp_path,
        store=FailsOnEvent(inner),
        payload_text=payload_text(command=secret_command),
    )
    raw = (inner.path).read_text(encoding="utf-8")
    assert "sk-live-abcdef0123456789" not in raw
    assert secret_command not in raw


# --- 동시 append -------------------------------------------------------------


def test_concurrent_recording_produces_intact_lines(tmp_path):
    store = AppendStore(tmp_path / "store")
    errors: list[Exception] = []

    def worker(i: int):
        try:
            record_guard_result(
                payload_text=payload_text(session_id=f"s-{i}"),
                guard_path="/fake/guard.sh",
                result=BLOCKED_STDERR,
                env={},
                store=store,
                now=NOW,
            )
        except Exception as exc:  # pragma: no cover - 계약 위반 감지용
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    result = store.load()
    assert not result.corrupt
    events = [r for r in result.records if isinstance(r, GuardEvent)]
    assert len(events) == 16
    assert len({e.event_id for e in events}) == 16


# --- 저장 위치 ---------------------------------------------------------------


def test_store_root_defaults_to_production_and_env_overrides(tmp_path):
    assert resolve_store_root({}) == production_root()
    assert resolve_store_root({STORE_ENV: str(tmp_path / "s")}) == tmp_path / "s"
