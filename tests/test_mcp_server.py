"""읽기 전용 MCP 증거 조회 서버 (T7-1, spec §3~§5, §8).

완료 조건 고정:
- 비노출 양성 대조: 홈 경로와 세션 식별자를 **실제로 심은** 픽스처로 세 도구와
  오류 경로의 전체 응답을 훑어, 원문이 0회이고 치환 결과가 실제로 나타나는지
  확인한다. 깨끗한 픽스처 스캔은 통과로 치지 않는다.
- 별칭 규칙: 한 응답 안에서 같은 세션=같은 별칭, 다른 세션=다른 별칭, 원문 0회.
- 읽기 전용: 세 도구 호출 전후로 store 디렉터리의 파일 목록·내용이 같고,
  store가 없을 때는 호출 뒤에도 디렉터리가 생기지 않는다.
- 도구 계약: guard_evidence 필수 내용, 미등록 가드·없는 버전 오류 경로,
  빈 store 정상 응답, get_report는 기존 보고서 생성 함수 출력과 동일.
- MCP 왕복 스모크: 초기화 → tools/list(3종) → 3종 호출 (SDK 제공 테스트 클라이언트).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError

from rejectbench import (
    AppendStore,
    CaptureStatus,
    Decision,
    EnforcementRef,
    JudgeCalibration,
    Origin,
    OriginEvidence,
    Utility,
    Verdict,
    append_calibration,
    generate_report,
)
from rejectbench.mcp_server import (
    GET_REPORT_TOOL,
    GUARD_EVIDENCE_TOOL,
    LIST_GUARDS_TOOL,
    OutputBoundary,
    build_server,
    render_guard_evidence,
    render_list_guards,
    render_report_text,
)
from tests.factories import (
    make_action,
    make_decision,
    make_event,
    make_review,
    make_spec,
    make_verdict,
    ts,
)

NOW = datetime(2026, 8, 2, 0, 0, 0, tzinfo=timezone.utc)

HOME = str(Path.home())
# 실제로 심는 값들 — 양성 대조의 핵심이다. 깨끗한 픽스처는 대조가 되지 않는다.
RAW_SESSION_A = "claude:sess-PLANTED-AAAA-0001"
RAW_SESSION_B = "codex:sess-PLANTED-BBBB-0002"
PLANTED_TARGET = f"{HOME}/workspace/reject-bench/reports/live.md"
PLANTED_SCRIPT = f"{HOME}/.rejectbench-absent-fixture-dir/protect-live-reports.sh"


# --- 도구 호출 헬퍼 (SDK 제공 인프로세스 클라이언트) ---------------------------


def call_tool(server, name: str, arguments: dict | None = None):
    async def run():
        async with Client(server) as client:
            return await client.call_tool(name, arguments or {})

    return asyncio.run(run())


def list_tool_names(server) -> list[str]:
    async def run():
        async with Client(server) as client:
            return [tool.name for tool in (await client.list_tools()).tools]

    return asyncio.run(run())


def result_text(result) -> str:
    return "".join(
        block.text for block in result.content if getattr(block, "type", "") == "text"
    )


def whole_result(result) -> str:
    """CallToolResult 전체를 문자열로 — 텍스트 블록 밖까지 훑기 위한 것."""
    return result.model_dump_json()


# --- 픽스처 ------------------------------------------------------------------


def planted_store(root: Path) -> AppendStore:
    """홈 경로·세션 식별자를 실제로 심은 store."""
    store = AppendStore(root)
    spec1 = make_spec(
        guard_id="planted-guard",
        version=1,
        purpose=f"{PLANTED_TARGET} 산출물을 덮어쓰는 사고를 막는다",
        policy=f"{HOME}/workspace 아래 live 보고서 쓰기를 차단한다",
        exceptions=(f"{HOME}/workspace/tmp 아래는 예외",),
        allow_examples=("보고서 읽기",),
        block_examples=(f"{PLANTED_TARGET} 덮어쓰기",),
        created_at=ts(0),
        enforcement_ref=EnforcementRef(
            script_path=PLANTED_SCRIPT, file_hash="sha256:" + "a" * 64
        ),
    )
    spec2 = make_spec(
        guard_id="planted-guard",
        version=2,
        purpose=f"{PLANTED_TARGET} 산출물을 덮어쓰는 사고를 막는다 (개정)",
        policy=f"{HOME}/workspace 아래 live 보고서 쓰기를 차단한다",
        exceptions=(),
        allow_examples=("보고서 읽기",),
        block_examples=(f"{PLANTED_TARGET} 덮어쓰기",),
        created_at=ts(1),
        enforcement_ref=EnforcementRef(
            script_path=PLANTED_SCRIPT, file_hash="sha256:" + "b" * 64
        ),
    )
    other = make_spec(guard_id="other-guard", version=1, created_at=ts(2))
    store.append(spec1)
    store.append(spec2)
    store.append(other)

    ev1 = make_event(
        spec1,
        event_id="ev-p1",
        session_id=RAW_SESSION_A,
        occurred_at=ts(10),
        action=make_action(tool_name="Write", command_verb=None, target_path=PLANTED_TARGET),
        reason=f"blocked: {PLANTED_TARGET} 쓰기 차단 (세션 {RAW_SESSION_A})",
    )
    ev2 = make_event(
        spec1,
        event_id="ev-p2",
        session_id=RAW_SESSION_B,
        occurred_at=ts(20),
        capture_status=CaptureStatus.PARTIAL,
        action=make_action(tool_name="Bash", command_verb="cp", target_path=PLANTED_TARGET),
        reason=f"blocked: {PLANTED_TARGET} 보호 (세션 {RAW_SESSION_B})",
        drift=True,
    )
    ev3 = make_event(
        spec1,
        event_id="ev-p3",
        session_id=RAW_SESSION_A,
        occurred_at=ts(30),
        action=make_action(tool_name="Write", command_verb=None, target_path=PLANTED_TARGET),
        reason=f"blocked: 재시도 (세션 {RAW_SESSION_A})",
    )
    for event in (ev1, ev2, ev3):
        store.append(event)

    vd1 = make_verdict(
        ev1,
        verdict_id="vd-p1",
        verdict=Verdict.CORRECT_BLOCK,
        reason=f"{PLANTED_TARGET} 는 정책 대상이다",
    )
    vd2 = make_verdict(
        ev2,
        verdict_id="vd-p2",
        verdict=Verdict.CORRECT_BLOCK,
        reason="정책 조항과 일치",
        model_settings_hash="sha256:" + "9" * 64,
    )
    store.append(vd1)
    store.append(vd2)
    store.append(
        make_review(
            ev1,
            review_id="rv-p1",
            utility=Utility.USEFUL,
            note=f"{PLANTED_TARGET} 를 지킬 뻔했다 (세션 {RAW_SESSION_A})",
        )
    )
    store.append(
        make_review(ev2, review_id="rv-p2", utility=Utility.UNNECESSARY, note="이미 백업했다")
    )
    store.append(
        make_decision(
            guard_id="planted-guard",
            decision_id="dc-p1",
            decision=Decision.KEEP,
            evidence_event_ids=("ev-p1", "ev-p2"),
            rationale=f"{PLANTED_TARGET} 보호 근거 — 세션 {RAW_SESSION_A}, {RAW_SESSION_B}",
        )
    )

    # 교정 사이드카: vd-p1 조합은 통과, vd-p2 조합은 실패 기록.
    append_calibration(
        root,
        JudgeCalibration(
            calibration_id="cal-pass",
            calibrated_at=ts(50),
            guard_spec_hash=vd1.guard_spec_hash,
            rubric_hash=vd1.rubric_hash,
            model_id=vd1.model_id,
            model_settings_hash=vd1.model_settings_hash,
            examples_total=4,
            examples_passed=4,
            passed=True,
            failures=(),
        ),
    )
    append_calibration(
        root,
        JudgeCalibration(
            calibration_id="cal-fail",
            calibrated_at=ts(51),
            guard_spec_hash=vd2.guard_spec_hash,
            rubric_hash=vd2.rubric_hash,
            model_id=vd2.model_id,
            model_settings_hash=vd2.model_settings_hash,
            examples_total=4,
            examples_passed=2,
            passed=False,
            failures=("예시 2건 불일치",),
        ),
    )
    (root / "baseline.json").write_text(
        json.dumps({"측정 경로": f"{HOME}/workspace/reject-bench/baseline"}, ensure_ascii=False),
        encoding="utf-8",
    )
    return store


def simple_store(root: Path) -> AppendStore:
    store = AppendStore(root)
    spec = make_spec(guard_id="guard-a", version=1, created_at=ts(0))
    store.append(spec)
    store.append(make_event(spec, event_id="ev-1", session_id="claude:s-1", occurred_at=ts(10)))
    return store


def snapshot(root: Path):
    if not root.exists():
        return None
    entries = []
    for path in sorted(root.rglob("*")):
        entries.append(
            (
                str(path.relative_to(root)),
                path.is_dir(),
                path.read_bytes() if path.is_file() else b"",
            )
        )
    return tuple(entries)


# --- §5 비노출 양성 대조 -------------------------------------------------------


def assert_fixture_really_planted(store: AppendStore) -> None:
    """양성 대조의 전제 — 원문이 store에 실제로 심겨 있어야 대조가 성립한다."""
    raw = store.path.read_text(encoding="utf-8")
    raw += (store.root / "calibration.jsonl").read_text(encoding="utf-8")
    raw += (store.root / "baseline.json").read_text(encoding="utf-8")
    assert HOME in raw, "픽스처에 홈 절대 경로가 심겨 있지 않다 — 깨끗한 스캔은 대조가 아니다"
    assert PLANTED_TARGET in raw
    assert PLANTED_SCRIPT in raw
    assert RAW_SESSION_A in raw
    assert RAW_SESSION_B in raw


def test_planted_fixture_actually_contains_the_secrets(tmp_path):
    assert_fixture_really_planted(planted_store(tmp_path / "store"))


def test_positive_control_no_home_path_or_session_id_in_any_response(tmp_path):
    """세 도구 + 오류 경로 전체 응답에 심은 원문이 0회, 치환 결과는 실제로 나온다."""
    store = planted_store(tmp_path / "store")
    assert_fixture_really_planted(store)
    server = build_server(store, now=NOW)

    # 텍스트 블록만이 아니라 CallToolResult 전체(직렬화 결과)를 훑는다.
    responses = {
        "list": whole_result(call_tool(server, LIST_GUARDS_TOOL)),
        "evidence": whole_result(
            call_tool(server, GUARD_EVIDENCE_TOOL, {"guard_id": "planted-guard"})
        ),
        "report": whole_result(call_tool(server, GET_REPORT_TOOL)),
    }
    # 오류 경로도 같은 경계를 지난다 — 홈 경로와 세션 원문을 심은 입력으로 부른다.
    error = call_tool(
        server,
        GUARD_EVIDENCE_TOOL,
        {"guard_id": f"{HOME}/없는가드-{RAW_SESSION_A}"},
    )
    assert error.is_error is True
    responses["error"] = whole_result(error)

    for label, text in responses.items():
        assert HOME not in text, f"{label}: 홈 절대 경로가 노출됐다"
        assert PLANTED_TARGET not in text, f"{label}: 심은 홈 경로 문자열이 노출됐다"
        assert PLANTED_SCRIPT not in text, f"{label}: enforcement_ref 홈 경로가 노출됐다"
        assert RAW_SESSION_A not in text, f"{label}: 세션 원문 A가 노출됐다"
        assert RAW_SESSION_B not in text, f"{label}: 세션 원문 B가 노출됐다"

    # 치환 결과가 실제로 나타난다 (경로 꼬리 보존 + 별칭).
    tail = PLANTED_TARGET.replace(HOME, "~")
    assert tail in responses["evidence"]
    assert "~/" in responses["report"]
    assert "S1" in responses["evidence"]
    assert "S2" in responses["evidence"]
    assert "~/없는가드-S1" in responses["error"]


def test_positive_control_covers_enforcement_reason_and_meaning_fields(tmp_path):
    """구현물 대조 사유와 의미 5필드도 같은 경계를 지난다."""
    store = planted_store(tmp_path / "store")
    payload = json.loads(render_guard_evidence(store, "planted-guard"))
    context = payload["context"]
    blob = json.dumps(context, ensure_ascii=False)
    assert HOME not in blob
    assert "~/" in context["enforcement"]["reason"]
    assert "~/" in context["purpose"]
    assert "~/" in context["policy"]


def test_error_text_is_single_line_without_paths_or_traceback(tmp_path):
    store = simple_store(tmp_path / "store")
    server = build_server(store, now=NOW)
    result = call_tool(server, GUARD_EVIDENCE_TOOL, {"guard_id": "no-such-guard"})
    text = result_text(result)
    assert result.is_error is True
    assert "\n" not in text
    assert "Traceback" not in text
    assert str(tmp_path) not in text
    assert "no-such-guard" in text


# --- §5 별칭 규칙 --------------------------------------------------------------


def test_session_alias_rules_within_one_response(tmp_path):
    store = planted_store(tmp_path / "store")
    payload = json.loads(render_guard_evidence(store, "planted-guard"))
    aliases = {row["event_id"]: row["session"] for row in payload["events"]}
    assert aliases["ev-p1"] == aliases["ev-p3"]  # 같은 세션 = 같은 별칭
    assert aliases["ev-p1"] != aliases["ev-p2"]  # 다른 세션 = 다른 별칭
    assert aliases["ev-p1"] == "S1"  # 첫 등장 순서
    assert aliases["ev-p2"] == "S2"
    blob = json.dumps(payload, ensure_ascii=False)
    assert RAW_SESSION_A not in blob
    assert RAW_SESSION_B not in blob


def test_alias_map_is_per_call_and_not_persisted(tmp_path):
    """별칭 표는 호출 안 메모리에만 있다 — 디스크에 남지 않는다."""
    root = tmp_path / "store"
    store = planted_store(root)
    before = snapshot(root)
    render_guard_evidence(store, "planted-guard")
    render_guard_evidence(store, "planted-guard")
    assert snapshot(root) == before
    boundary = OutputBoundary(session_ids=(RAW_SESSION_A,))
    assert boundary.sanitize(RAW_SESSION_A) == "S1"
    fresh = OutputBoundary(session_ids=(RAW_SESSION_B, RAW_SESSION_A))
    assert fresh.sanitize(RAW_SESSION_B) == "S1"  # 호출마다 새로 시작한다


# --- §8 읽기 전용 --------------------------------------------------------------


def test_store_is_unchanged_by_all_tool_calls(tmp_path):
    root = tmp_path / "store"
    store = planted_store(root)
    server = build_server(store, now=NOW)
    before = snapshot(root)
    call_tool(server, LIST_GUARDS_TOOL)
    call_tool(server, GUARD_EVIDENCE_TOOL, {"guard_id": "planted-guard"})
    call_tool(server, GUARD_EVIDENCE_TOOL, {"guard_id": "planted-guard", "version": 1})
    call_tool(server, GET_REPORT_TOOL)
    call_tool(server, GUARD_EVIDENCE_TOOL, {"guard_id": "nope"})
    assert snapshot(root) == before


def test_absent_store_directory_is_not_created(tmp_path):
    root = tmp_path / "absent"
    server = build_server(AppendStore(root), now=NOW)
    call_tool(server, LIST_GUARDS_TOOL)
    call_tool(server, GET_REPORT_TOOL)
    call_tool(server, GUARD_EVIDENCE_TOOL, {"guard_id": "nope"})
    assert not root.exists()


def test_guard_script_bytes_are_read_never_executed(tmp_path):
    """등록된 enforcement_ref 스크립트는 바이트로만 읽는다 — 실행 흔적이 없다."""
    root = tmp_path / "store"
    script = tmp_path / "guard.sh"
    marker = tmp_path / "executed.marker"
    script.write_text(
        "#!/bin/sh\n" f"touch {marker}\n", encoding="utf-8"
    )
    script.chmod(0o755)
    store = AppendStore(root)
    spec = make_spec(
        guard_id="script-guard",
        version=1,
        created_at=ts(0),
        enforcement_ref=EnforcementRef(script_path=str(script), file_hash="sha256:" + "c" * 64),
    )
    store.append(spec)
    payload = json.loads(render_guard_evidence(store, "script-guard"))
    assert payload["context"]["enforcement"]["status"] == "drift"
    assert not marker.exists()


# --- §4.1 list_guards ---------------------------------------------------------


def test_list_guards_returns_all_registered_guards(tmp_path):
    store = planted_store(tmp_path / "store")
    guards = json.loads(render_list_guards(store))["guards"]
    by_id = {g["guard_id"]: g for g in guards}
    assert set(by_id) == {"planted-guard", "other-guard"}
    assert by_id["planted-guard"]["latest_version"] == 2
    assert by_id["planted-guard"]["versions"] == [1, 2]
    assert by_id["planted-guard"]["project"] == "reject-bench"


def test_list_guards_on_empty_store_is_empty_list_not_error(tmp_path):
    result = call_tool(build_server(AppendStore(tmp_path / "empty"), now=NOW), LIST_GUARDS_TOOL)
    assert result.is_error in (False, None)
    assert json.loads(result_text(result))["guards"] == []


# --- §4.2 guard_evidence ------------------------------------------------------


def test_guard_evidence_required_content(tmp_path):
    store = planted_store(tmp_path / "store")
    payload = json.loads(render_guard_evidence(store, "planted-guard"))

    context = payload["context"]
    for field in (
        "purpose",
        "policy",
        "exceptions",
        "allow_examples",
        "block_examples",
        "version",
        "content_hash",
        "versions",
        "enforcement",
    ):
        assert field in context, field
    assert context["version"] == 2  # 생략 시 최신 버전 맥락
    assert context["enforcement"]["status"] in {"in_sync", "drift", "unverifiable"}
    assert context["enforcement"]["reason"]

    sessions = payload["sessions"]
    assert sessions["operation_session_count"] == 2
    assert sessions["decidable_session_count"] == 2
    assert sessions["guard_decidable"] is True
    assert "operation 세션" in sessions["criterion"]

    events = payload["events"]
    assert [row["event_id"] for row in events] == ["ev-p1", "ev-p2", "ev-p3"]
    first = events[0]
    assert first["occurred_at"] == ts(10).isoformat()
    assert first["effective_origin"] == Origin.OPERATION.value
    assert first["capture_status"] == CaptureStatus.COMPLETE.value
    assert set(first["action"]) == {"tool_name", "command_verb", "target_path", "heredoc"}
    assert first["reason"]
    assert first["policy_verdict"]["verdict"] == Verdict.CORRECT_BLOCK.value
    assert first["policy_verdict"]["model_id"] == "judge-model-1"
    assert first["policy_verdict"]["judged_at"]
    assert first["policy_verdict"]["reason"]
    assert first["policy_verdict"]["calibration_status"] == "passed"
    assert first["utility_review"]["utility"] == Utility.USEFUL.value
    assert first["utility_review"]["note"]
    assert first["utility_review"]["reviewed_at"]
    assert first["decidable"] is True
    assert set(first["markers"]) == {"post_remove", "drift", "partial"}

    second = events[1]
    assert second["policy_verdict"]["calibration_status"] == "failed"
    assert second["markers"]["partial"] is True
    assert second["markers"]["drift"] is True

    third = events[2]
    assert third["policy_verdict"] == {"status": "미처리"}
    assert third["utility_review"] == {"status": "미처리"}
    assert third["decidable"] is False

    decisions = payload["decisions"]
    assert [d["decision_id"] for d in decisions] == ["dc-p1"]
    decision = decisions[0]
    assert decision["decision"] == Decision.KEEP.value
    assert decision["decided_at"]
    assert decision["evidence_event_ids"] == ["ev-p1", "ev-p2"]
    assert decision["rationale"]
    assert decision["resulting_guard_version"] is None
    assert decision["countable"] is True
    assert decision["no_event_guard"] is False
    assert decision["exclusion_reasons"] == []

    assert payload["record_health"]["corrupt_lines"] == 0


def test_guard_evidence_calibration_status_none_when_uncalibrated(tmp_path):
    root = tmp_path / "store"
    store = AppendStore(root)
    spec = make_spec(guard_id="guard-a", version=1, created_at=ts(0))
    store.append(spec)
    event = make_event(spec, event_id="ev-1", session_id="claude:s-1", occurred_at=ts(10))
    store.append(event)
    store.append(make_verdict(event, verdict_id="vd-1"))
    payload = json.loads(render_guard_evidence(store, "guard-a"))
    assert payload["events"][0]["policy_verdict"]["calibration_status"] == "none"


def test_guard_evidence_specific_version_uses_that_spec_context(tmp_path):
    store = planted_store(tmp_path / "store")
    payload = json.loads(render_guard_evidence(store, "planted-guard", version=1))
    assert payload["context"]["version"] == 1
    assert payload["context"]["versions"] == [1, 2]
    assert payload["context"]["exceptions"] != []
    latest = json.loads(render_guard_evidence(store, "planted-guard", version=2))
    assert latest["context"]["content_hash"] != payload["context"]["content_hash"]
    # 사건·결정 목록은 버전과 무관하게 가드 전체다.
    assert len(payload["events"]) == len(latest["events"]) == 3


def test_guard_evidence_reports_corrupt_lines(tmp_path):
    root = tmp_path / "store"
    store = simple_store(root)
    with open(store.path, "ab") as handle:
        handle.write(b"{not json}\n")
    payload = json.loads(render_guard_evidence(store, "guard-a"))
    assert payload["record_health"]["corrupt_lines"] == 1


def test_guard_evidence_unregistered_guard_is_tool_error(tmp_path):
    store = simple_store(tmp_path / "store")
    with pytest.raises(ToolError):
        render_guard_evidence(store, "ghost-guard")
    result = call_tool(build_server(store, now=NOW), GUARD_EVIDENCE_TOOL, {"guard_id": "ghost-guard"})
    assert result.is_error is True
    assert "ghost-guard" in result_text(result)


def test_guard_evidence_unknown_version_is_tool_error(tmp_path):
    store = simple_store(tmp_path / "store")
    result = call_tool(
        build_server(store, now=NOW), GUARD_EVIDENCE_TOOL, {"guard_id": "guard-a", "version": 9}
    )
    assert result.is_error is True
    text = result_text(result)
    assert "\n" not in text
    assert "9" in text


# --- §4.3 get_report ----------------------------------------------------------


def test_get_report_is_identical_to_generate_report(tmp_path):
    store = planted_store(tmp_path / "store")
    expected = generate_report(store, now=NOW)
    assert render_report_text(store, now=NOW) == expected
    assert result_text(call_tool(build_server(store, now=NOW), GET_REPORT_TOOL)) == expected


def test_get_report_on_missing_store_returns_unverified_state(tmp_path):
    root = tmp_path / "absent"
    result = call_tool(build_server(AppendStore(root), now=NOW), GET_REPORT_TOOL)
    assert result.is_error in (False, None)
    text = result_text(result)
    assert text == generate_report(AppendStore(root), now=NOW)
    assert "# Reject Bench 보고서" in text
    assert not root.exists()


# --- §8 MCP 왕복 스모크 --------------------------------------------------------


def test_mcp_round_trip_lists_and_calls_all_three_tools(tmp_path):
    store = planted_store(tmp_path / "store")
    server = build_server(store, now=NOW)
    assert set(list_tool_names(server)) == {
        LIST_GUARDS_TOOL,
        GUARD_EVIDENCE_TOOL,
        GET_REPORT_TOOL,
    }
    for name, arguments in (
        (LIST_GUARDS_TOOL, {}),
        (GUARD_EVIDENCE_TOOL, {"guard_id": "planted-guard"}),
        (GET_REPORT_TOOL, {}),
    ):
        result = call_tool(server, name, arguments)
        assert result.is_error in (False, None), name
        assert result_text(result)


def test_freshness_reloads_store_on_every_call(tmp_path):
    root = tmp_path / "store"
    store = simple_store(root)
    server = build_server(store, now=NOW)
    before = json.loads(result_text(call_tool(server, LIST_GUARDS_TOOL)))["guards"]
    assert [g["guard_id"] for g in before] == ["guard-a"]
    store.append(make_spec(guard_id="guard-b", version=1, created_at=ts(3)))
    after = json.loads(result_text(call_tool(server, LIST_GUARDS_TOOL)))["guards"]
    assert {g["guard_id"] for g in after} == {"guard-a", "guard-b"}


def test_v1_core_modules_do_not_import_the_sdk():
    package = Path(__file__).resolve().parents[1] / "rejectbench"
    importers = [
        path.name
        for path in sorted(package.glob("*.py"))
        if path.name != "mcp_server.py" and "mcp" in path.read_text(encoding="utf-8")
    ]
    assert importers == [], f"SDK 의존이 새 모듈 밖으로 샜다: {importers}"
