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
    SessionIdFormat,
    enforcement_ref_for,
    production_root,
)
from rejectbench import recorder
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


# --- 차단 사유 절단 (spec §3) -------------------------------------------------
#
# 계약이 고정한 숫자는 테스트가 리터럴로 못 박는다 — 구현 상수를 그대로 읽어
# 비교하면 상한을 바꿔도 테스트가 따라 움직여 회귀를 못 잡는다. 상한·하한
# 상수끼리의 정합만 마지막 테스트가 따로 본다.

MAX = 4000  # 본문 상한 (마커 별도)
FLOOR = 2000  # 하한 = 상한의 50%
MARKER = "…[truncated]"

HOME = "/Users/testuser"
SESSION_RAW = "11111111-2222-3333-4444-555555555555"
SESSION_ID = f"claude:{SESSION_RAW}"
UNIT = "filler "  # 7자, 공백류로 끝난다
DENSE = "x"  # 공백류가 없고 scrub의 hex·base64형 판정에도 걸리지 않는 채움


def blocked(reason: str) -> GuardResult:
    return GuardResult(exit_code=2, stdout="", stderr=reason)


def spaced(length: int) -> str:
    """`UNIT` 반복으로 정확히 `length`자 — 마지막 한 칸이 유일한 끝 공백류다."""
    assert length % len(UNIT) == 0
    return UNIT * (length // len(UNIT))


def dense(length: int) -> str:
    return DENSE * length


def truncate_case(
    tmp_path: Path, text: str, *, home: str = HOME, session_raw: str = SESSION_RAW
) -> str:
    store, outcome = record(
        tmp_path,
        payload_text=payload_text(session_id=session_raw),
        result=blocked(text),
        env={"HOME": home},
    )
    assert outcome.recorded
    (event,) = loaded_events(store)
    return event.reason


def body_of(stored: str) -> str:
    assert stored.endswith(MARKER)
    return stored[: -len(MARKER)]


def crosses_cut(text: str, stored: str, value: str) -> bool:
    """저장 본문 길이가 원문 속 `value` 등장을 가로지르는가 — 계약이 금지하는 것.

    "본문 끝이 민감값의 진부분 접두인가"로 보면 안 된다. 홈 경로가 `/`로
    시작하므로 본문이 정당하게 `/`로 끝나기만 해도 조각으로 오판한다 —
    계약이 금지하는 것은 접미-접두 일치가 아니라 **등장의 가로지름**이다.
    """
    cut = len(body_of(stored))
    start = text.find(value)
    while start != -1:
        if start < cut < start + len(value):
            return True
        start = text.find(value, start + 1)
    return False


def test_short_reason_is_stored_verbatim(tmp_path):
    text = dense(10) + " " + HOME + " " + SESSION_ID
    assert truncate_case(tmp_path, text) == text


def test_reason_of_exactly_the_cap_is_not_truncated(tmp_path):
    text = dense(MAX)
    stored = truncate_case(tmp_path, text)
    assert stored == text
    assert MARKER not in stored


def test_one_char_over_the_cap_is_truncated(tmp_path):
    assert truncate_case(tmp_path, dense(MAX + 1)) == dense(MAX) + MARKER


def test_truncation_retreats_to_the_last_whitespace_unit(tmp_path):
    """공백 구분 단위(`str.split()` 경계)를 반토막 내지 않는다."""
    text = spaced(3990) + dense(500)  # 마지막 공백류는 index 3989 한 칸
    stored = truncate_case(tmp_path, text)
    assert stored == text[:3989] + MARKER
    assert stored.endswith("filler" + MARKER)
    assert len(body_of(stored)) <= MAX  # 되물림은 본문을 줄이기만 한다


def test_home_path_is_never_cut_in_half(tmp_path):
    needle = HOME + "/workspace/reject-bench/evidence.md"
    text = spaced(3990) + needle + dense(200)
    assert 3990 < MAX < 3990 + len(needle)  # 픽스처가 실제로 경계를 가로지른다
    stored = truncate_case(tmp_path, text)
    assert stored == text[:3989] + MARKER
    assert HOME not in stored
    assert not crosses_cut(text, stored, needle)


def test_composite_session_id_is_never_cut_in_half(tmp_path):
    text = spaced(3990) + SESSION_ID + dense(200)
    assert 3990 < MAX < 3990 + len(SESSION_ID)
    stored = truncate_case(tmp_path, text)
    assert stored == text[:3989] + MARKER
    assert SESSION_RAW not in stored
    assert not crosses_cut(text, stored, SESSION_ID)


def test_raw_session_id_survives_input_without_any_whitespace(tmp_path):
    """민감값 검사는 공백 되물림의 보조가 아니라 항상 도는 규칙이다."""
    text = dense(3990) + SESSION_RAW + dense(200)
    stored = truncate_case(tmp_path, text)
    assert stored == dense(3990) + MARKER
    assert not crosses_cut(text, stored, SESSION_RAW)


def test_home_path_survives_input_without_any_whitespace(tmp_path):
    text = dense(3990) + HOME + dense(200)
    assert 3990 < MAX < 3990 + len(HOME)
    assert truncate_case(tmp_path, text) == dense(3990) + MARKER


def test_composite_session_id_survives_input_without_any_whitespace(tmp_path):
    text = dense(3990) + SESSION_ID + dense(200)
    stored = truncate_case(tmp_path, text)
    assert stored == dense(3990) + MARKER  # 복합값 시작이 원본 부분 시작보다 앞이다


def test_sensitive_value_holding_whitespace_is_checked_after_the_retreat(tmp_path):
    """공백 되물림이 민감값 한가운데로 떨어지는 경로 — 그 뒤에도 검사가 돈다."""
    home = "/Users/test user"  # 값 안에 공백류가 있다
    head = dense(3984) + " "  # 3985자, 앞쪽 공백류는 이 한 칸뿐
    text = head + home + dense(200)
    assert len(head) < MAX < len(head) + len(home)
    stored = truncate_case(tmp_path, text, home=home)
    assert stored == text[: len(head)] + MARKER
    assert home not in stored
    assert not crosses_cut(text, stored, home)


def test_retreat_repeats_until_no_sensitive_value_is_crossed(tmp_path):
    """되물린 자리가 또 다른 민감값 한가운데면 다시 되물린다 — 고정점."""
    home = "/Users/test-run"
    raw = "n1111111-2222-3333-4444-555555555555"
    text = dense(3981) + "/Users/test-ru" + raw + dense(100)
    assert text[3981 : 3981 + len(home)] == home  # 두 민감값이 한 글자 겹친다
    assert text[3995 : 3995 + len(raw)] == raw
    stored = truncate_case(tmp_path, text, home=home, session_raw=raw)
    assert stored == dense(3981) + MARKER


def test_whitespace_retreat_is_dropped_when_it_eats_half_the_body(tmp_path):
    """공백이 앞쪽 한 곳뿐이면 정돈을 버리고 맹목 절단으로 폴백한다."""
    text = dense(5) + " " + dense(MAX + 500)
    assert truncate_case(tmp_path, text) == text[:MAX] + MARKER


def test_body_exactly_at_the_lower_bound_keeps_the_retreat(tmp_path):
    text = dense(FLOOR) + " " + dense(MAX)
    assert truncate_case(tmp_path, text) == text[:FLOOR] + MARKER


def test_body_one_char_under_the_lower_bound_falls_back(tmp_path):
    text = dense(FLOOR - 1) + " " + dense(MAX)
    assert truncate_case(tmp_path, text) == text[:MAX] + MARKER


def test_fallback_still_avoids_cutting_a_sensitive_value(tmp_path):
    """폴백 결과에도 민감값 고정점 검사가 적용된다."""
    text = dense(5) + " " + dense(3984) + SESSION_RAW + dense(100)
    stored = truncate_case(tmp_path, text)
    assert stored == text[:3990] + MARKER
    assert not crosses_cut(text, stored, SESSION_RAW)


def test_input_without_any_whitespace_falls_back_to_the_blind_cut(tmp_path):
    assert truncate_case(tmp_path, dense(MAX + 500)) == dense(MAX) + MARKER


def test_newline_is_a_whitespace_boundary(tmp_path):
    """가드 stderr는 다중 줄이 흔하다 — 개행이 경계다.

    개행이 **앞 4000자 안의 마지막** 공백류여야 절단점을 실제로 정한다. 탭이
    뒤에 오면 이 절은 공허해진다 — 개행을 공백류로 보지 않는 구현도 통과한다.
    """
    text = dense(3000) + "\t" + dense(500) + "\n" + dense(1000)
    stored = truncate_case(tmp_path, text)
    assert stored == text[:3501] + MARKER
    assert stored.endswith(DENSE + MARKER)


def test_tab_is_a_whitespace_boundary(tmp_path):
    text = dense(3000) + "\n" + dense(500) + "\t" + dense(1000)
    stored = truncate_case(tmp_path, text)
    assert stored == text[:3501] + MARKER
    assert stored.endswith(DENSE + MARKER)


# 음성 대조 — 되물림의 **상한** 경계. "가로지를 때만" 되물린다는 조건이 없으면
# 사유 앞머리에 홈 경로가 한 번 나오기만 해도 본문이 통째로 사라지고, 그 붕괴는
# 양성 픽스처만으로는 green으로 지나간다.


def test_occurrence_inside_the_body_is_not_retreated(tmp_path):
    """본문 안에 온전히 든 민감값 등장은 절단점을 당기지 않는다."""
    head = HOME + " " + UNIT * 567 + "abcd "  # 3990자, 끝 공백류는 index 3989
    text = head + dense(500)
    assert len(head) == 3990 and text.startswith(HOME)
    stored = truncate_case(tmp_path, text)
    assert stored == text[:3989] + MARKER
    assert stored.startswith(HOME)  # 되물림이 앞머리 등장까지 밀지 않았다
    assert len(body_of(stored)) > FLOOR


def test_occurrence_ending_exactly_at_the_cut_is_kept_whole(tmp_path):
    """등장이 절단점에서 정확히 끝나면 가로지름이 아니다 — 되물리지 않는다."""
    text = dense(3954) + SESSION_RAW + " " + dense(500)
    assert text[3954 : 3954 + len(SESSION_RAW)] == SESSION_RAW
    stored = truncate_case(tmp_path, text)
    assert stored == text[:3990] + MARKER  # 세션 ID가 온전히 본문에 남는다
    assert stored.endswith(SESSION_RAW + MARKER)


def test_occurrence_starting_exactly_at_the_cut_is_not_retreated(tmp_path):
    """등장이 절단점에서 시작하면 조각이 안 생긴다 — 더 되물릴 이유가 없다."""
    text = dense(MAX) + SESSION_RAW + dense(100)
    assert truncate_case(tmp_path, text) == dense(MAX) + MARKER


def test_placeholder_session_path_still_protects_the_home_path(tmp_path):
    """맥락 부재(자리표시) 경로에서도 절단과 홈 경로 보호가 돈다."""
    text = dense(3990) + HOME + dense(200)
    store, outcome = record(
        tmp_path,
        payload_text=payload_text(session_id=None),
        result=blocked(text),
        env={"HOME": HOME},
    )
    assert outcome.recorded
    (event,) = loaded_events(store)
    assert event.capture_status is CaptureStatus.PARTIAL
    assert event.reason == dense(3990) + MARKER


def test_env_without_home_still_records_and_truncates(tmp_path):
    """홈을 인자로 못 받아도 절단은 돌고 기록은 성립한다."""
    store, outcome = record(
        tmp_path,
        payload_text=payload_text(session_id=SESSION_RAW),
        result=blocked(dense(MAX + 600)),
        env={},
    )
    assert outcome.recorded
    (event,) = loaded_events(store)
    assert event.reason == dense(MAX) + MARKER


def test_truncation_failure_falls_back_to_blind_cut_and_still_records(tmp_path, monkeypatch):
    """절단 계산이 죽어도 사건은 LossRecord로 강등되지 않는다 (spec §3.5)."""

    def boom(*args, **kwargs):
        raise ValueError("절단 계산 결함")

    monkeypatch.setattr(recorder, "_cut_point", boom)
    text = dense(FLOOR) + " " + dense(MAX)  # 정상 경로라면 FLOOR에서 잘린다
    store, outcome = record(
        tmp_path,
        payload_text=payload_text(session_id=SESSION_RAW),
        result=blocked(text),
        env={"HOME": HOME},
    )
    assert outcome.blocked and outcome.recorded and not outcome.loss_recorded
    result = store.load()
    assert not [r for r in result.records if isinstance(r, LossRecord)]
    (event,) = [r for r in result.records if isinstance(r, GuardEvent)]
    assert event.reason == text[:MAX] + MARKER  # 되물림 없는 현행 절단


def test_truncation_bounds_are_one_ratio_apart():
    """상한과 하한이 따로 놀면 조용히 깨진다 — 세 값을 리터럴로 못 박는다.

    "하한을 상한의 비율로 계산한다"는 코드 형태 요구라 값 단언으로는 잡히지
    않는다 — `_MIN_REASON`을 2000으로 하드코딩해도 비율 식은 참으로 남는다.
    그래서 파생식을 되풀이하지 않고 값을 각각 고정한다: 상한만 바꾸고 하한을
    잊으면 `_MAX_REASON` 단언에서 걸려 계약 개정 없이는 지나가지 못한다.
    """
    assert recorder._MAX_REASON == MAX
    assert recorder._MIN_REASON_RATIO == 0.5
    assert recorder._MIN_REASON == FLOOR
    assert recorder._TRUNCATION_MARKER == MARKER


# --- 세션 ID 적재 형식 (003 spec §4) -------------------------------------------
#
# 이 태스크는 관찰만 더한다 — 저장 세션 ID 값·origin 규칙·가드 발동 결과는 어떤
# 경로에서도 불변이고, 검사가 예외를 던져도 사건은 LossRecord로 강등되지 않는다.


def test_uuid_session_id_is_recorded_as_conforming(tmp_path):
    store, _ = record(tmp_path, payload_text=payload_text(session_id=SESSION_RAW))
    (event,) = loaded_events(store)
    assert event.session_id == SESSION_ID
    assert event.session_id_format is SessionIdFormat.CONFORMING


@pytest.mark.parametrize(
    "raw",
    [
        "abcdefg",  # 7자
        "a" * 129,  # 129자
        "abcd.efgh",  # 금지 문자
        "abcd efgh",  # 공백도 금지 문자다
    ],
)
def test_nonconforming_raw_id_is_flagged_but_everything_else_is_unchanged(tmp_path, raw):
    store, outcome = record(tmp_path, payload_text=payload_text(session_id=raw))
    assert outcome.blocked and outcome.recorded
    (event,) = loaded_events(store)
    assert event.session_id_format is SessionIdFormat.NONCONFORMING
    assert event.session_id == f"claude:{raw}"  # 저장값 불변 — 정규화·거부 없음
    assert event.origin is Origin.OPERATION
    assert event.origin_evidence is OriginEvidence.DEFAULT_INHERITED
    assert event.capture_status is CaptureStatus.COMPLETE


def test_placeholder_session_is_unchecked_not_nonconforming(tmp_path):
    """자리표시(맥락 부재)는 검사 대상이 아니다 — `unknown`이 7자라서가 아니다."""
    store, _ = record(tmp_path, payload_text=payload_text(session_id=None))
    (event,) = loaded_events(store)
    assert event.session_id == "claude:unknown"
    assert event.session_id_format is SessionIdFormat.UNCHECKED
    assert event.capture_status is CaptureStatus.PARTIAL


def test_format_check_failure_records_unchecked_and_changes_nothing_else(tmp_path, monkeypatch):
    """검사 술어가 죽어도 사건은 정상 기록된다 (spec §4.6) — LossRecord 강등 없음."""

    def boom(*args, **kwargs):
        raise ValueError("검사 결함")

    monkeypatch.setattr(recorder, "session_id_format", boom)
    store, outcome = record(tmp_path, payload_text=payload_text(session_id=SESSION_RAW))
    assert outcome.blocked and outcome.recorded and not outcome.loss_recorded
    result = store.load()
    assert not result.corrupt
    assert not [r for r in result.records if isinstance(r, LossRecord)]
    (event,) = [r for r in result.records if isinstance(r, GuardEvent)]
    assert event.session_id_format is SessionIdFormat.UNCHECKED
    assert event.session_id == SESSION_ID
    assert event.origin is Origin.OPERATION
    assert event.origin_evidence is OriginEvidence.DEFAULT_INHERITED
    assert event.capture_status is CaptureStatus.COMPLETE


def test_placeholder_raw_part_is_not_a_truncation_needle(tmp_path):
    """needle을 분해 함수로 모아도 자리표시 `unknown`은 민감값이 아니다.

    `unknown`이 needle이면 절단점이 3996으로 되물린다. 자리표시는 E3에서도
    준수 부류가 아니고, 일반 단어를 민감값으로 삼으면 사유가 부당하게 줄어든다.
    공백류가 없는 입력이라 정상 경로는 맹목 절단점(4000) 그대로다.
    """
    text = dense(3996) + "unknown" + dense(200)
    assert 3996 < MAX < 3996 + len("unknown")  # 픽스처가 실제로 경계를 가로지른다
    store, outcome = record(
        tmp_path,
        payload_text=payload_text(session_id=None),
        result=blocked(text),
        env={"HOME": HOME},
    )
    assert outcome.recorded
    (event,) = loaded_events(store)
    assert event.reason == text[:MAX] + MARKER
    assert event.reason != dense(3996) + MARKER
