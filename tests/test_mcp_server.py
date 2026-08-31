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
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters
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
from rejectbench import decision as decision_module
from rejectbench import mcp_server
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
from rejectbench.store import production_root
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


def tool_input_schemas(server) -> dict[str, dict]:
    """클라이언트가 실제로 보는 `tools/list` 입력 스키마."""

    async def run():
        async with Client(server) as client:
            return {
                tool.name: tool.input_schema for tool in (await client.list_tools()).tools
            }

    return asyncio.run(run())


def stdio_tool_input_schemas(store_root: Path, cwd: Path) -> dict[str, dict]:
    """stdio 하위 프로세스로 실제 서버를 띄워 받은 `tools/list` 입력 스키마.

    인프로세스 클라이언트는 같은 파이썬 객체를 보므로, 직렬화를 거친 실제 왕복에서도
    같은 계약이 실리는지는 따로 확인해야 한다.
    """
    repo_root = Path(__file__).resolve().parents[1]
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "rejectbench.mcp_server", "--store", str(store_root)],
        env={**os.environ, "PYTHONPATH": str(repo_root)},
        cwd=str(cwd),
    )

    async def run():
        async with Client(params, read_timeout_seconds=60) as client:
            return {
                tool.name: tool.input_schema for tool in (await client.list_tools()).tools
            }

    # 하위 프로세스가 남지 않도록 상한을 건다 — 클라이언트가 종료 시 프로세스를 거둔다.
    return asyncio.run(asyncio.wait_for(run(), timeout=120))


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


# --- §5 인자 검증도 같은 경계를 지난다 -----------------------------------------

#: SDK(pydantic)가 인자를 먼저 검증하면 그 오류 텍스트는 경계를 지나지 않는다.
#: 그래서 도구 인자는 무엇이든 받고, 검증은 도구 본문 안에서 한다.
BAD_ARGUMENTS = [
    ("version-문자열", {"guard_id": "planted-guard", "version": f"{HOME}/oops-{RAW_SESSION_A}"}),
    ("version-실수", {"guard_id": "planted-guard", "version": 1.5}),
    ("version-객체", {"guard_id": "planted-guard", "version": {"v": RAW_SESSION_B}}),
    ("guard_id-숫자", {"guard_id": 12345}),
    ("guard_id-목록", {"guard_id": [f"{HOME}/x", RAW_SESSION_A]}),
    ("guard_id-객체", {"guard_id": {"raw": RAW_SESSION_A}}),
    ("guard_id-null", {"guard_id": None}),
    ("guard_id-공백", {"guard_id": "   "}),
    ("guard_id-누락", {}),
    ("guard_id-누락+version에-원문", {"version": f"{HOME}/{RAW_SESSION_B}"}),
]


@pytest.mark.parametrize(
    "arguments", [args for _, args in BAD_ARGUMENTS], ids=[label for label, _ in BAD_ARGUMENTS]
)
def test_bad_arguments_never_bypass_the_output_boundary(tmp_path, arguments):
    """클라이언트가 보낼 수 있는 어떤 인자도 경계 밖 텍스트를 만들지 못한다."""
    store = planted_store(tmp_path / "store")
    result = call_tool(build_server(store, now=NOW), GUARD_EVIDENCE_TOOL, arguments)
    text = result_text(result)
    assert result.is_error is True
    assert text
    assert "\n" not in text
    assert HOME not in text
    assert RAW_SESSION_A not in text
    assert RAW_SESSION_B not in text
    assert "pydantic" not in text
    assert "Traceback" not in text


def test_bad_argument_echo_is_sanitized_not_dropped(tmp_path):
    """되돌려 주는 입력 메아리는 진단용이라 남기되, 경계를 지난 형태여야 한다."""
    store = planted_store(tmp_path / "store")
    result = call_tool(
        build_server(store, now=NOW),
        GUARD_EVIDENCE_TOOL,
        {"guard_id": [f"{HOME}/x", RAW_SESSION_A]},
    )
    text = result_text(result)
    assert "~/x" in text
    assert "S1" in text


def prefix_fragments(needle: str, minimum: int = 6) -> list[str]:
    """`needle`의 접두 조각들 (긴 것부터) — 조각 하나라도 남으면 경계가 새는 것이다."""
    return [needle[:n] for n in range(len(needle), minimum - 1, -1)]


def test_error_echo_is_truncated_only_after_sanitization(tmp_path):
    """메아리 절단은 반드시 정화 **뒤**다.

    먼저 자르면 잘린 조각(`/Users/ia…`, `claude:ses…`)이 온전한 일치가 아니게 되어
    치환이 그 조각을 놓친다. 새는 구간은 상한 언저리뿐이라 길이 하나만 보는
    테스트로는 잡히지 않으므로, 상한을 걸치는 패딩 길이를 훑는다.
    """
    store = planted_store(tmp_path / "store")
    server = build_server(store, now=NOW)
    leaks: list[tuple[str, int, str]] = []
    for needle in (RAW_SESSION_A, f"{HOME}/x"):
        for pad in range(100, 132):
            text = result_text(
                call_tool(
                    server,
                    GUARD_EVIDENCE_TOOL,
                    {"guard_id": "planted-guard", "version": "P" * pad + needle},
                )
            )
            assert "\n" not in text, (needle, pad)
            # 상한은 그대로 산다 (여유분은 SDK 접두 + 사유 문구 몫이다).
            assert len(text) <= mcp_server.MAX_ECHO + 60, (needle, pad, len(text))
            leaked = next((frag for frag in prefix_fragments(needle) if frag in text), None)
            if leaked is not None:
                leaks.append((needle[:12], pad, leaked))
    assert leaks == [], f"정화 전 절단으로 원문 조각이 살아 나갔다: {leaks[:5]}"

    # 상한이 실제로 문다 — 아주 긴 입력도 한 줄 안에 접힌다.
    huge = result_text(
        call_tool(
            server,
            GUARD_EVIDENCE_TOOL,
            {"guard_id": "planted-guard", "version": "P" * 5000 + RAW_SESSION_A},
        )
    )
    assert len(huge) <= mcp_server.MAX_ECHO + 60
    assert RAW_SESSION_A[:6] not in huge


def test_short_error_echo_still_shows_the_substitution(tmp_path):
    """자르지 않아도 되는 길이에서는 치환 결과가 그대로 보인다 (양성 대조)."""
    store = planted_store(tmp_path / "store")
    server = build_server(store, now=NOW)
    text = result_text(
        call_tool(
            server,
            GUARD_EVIDENCE_TOOL,
            {"guard_id": "planted-guard", "version": f"{HOME}/x-{RAW_SESSION_A}"},
        )
    )
    assert "~/x-S1" in text


@pytest.mark.parametrize("name", [LIST_GUARDS_TOOL, GET_REPORT_TOOL])
def test_argument_free_tools_tolerate_unexpected_arguments(tmp_path, name):
    """입력이 없는 도구도 낯선 인자로 SDK 오류를 내지 않는다."""
    store = planted_store(tmp_path / "store")
    result = call_tool(
        build_server(store, now=NOW),
        name,
        {"guard_id": f"{HOME}/{RAW_SESSION_A}", "unexpected": [RAW_SESSION_B]},
    )
    assert result.is_error in (False, None)
    text = whole_result(result)
    assert HOME not in text
    assert RAW_SESSION_A not in text
    assert RAW_SESSION_B not in text


def test_tools_list_keeps_guard_id_required_and_version_optional(tmp_path):
    """인자를 느슨하게 받아도 발행 스키마의 계약은 그대로다."""
    schemas = tool_input_schemas(build_server(planted_store(tmp_path / "store"), now=NOW))
    evidence = schemas[GUARD_EVIDENCE_TOOL]
    assert evidence["required"] == ["guard_id"]
    properties = evidence["properties"]
    assert set(properties) == {"guard_id", "version"}
    assert "default" not in properties["guard_id"]
    assert properties["version"]["default"] is None
    assert properties["guard_id"]["description"]
    assert properties["version"]["description"]
    for name in (LIST_GUARDS_TOOL, GET_REPORT_TOOL):
        assert schemas[name].get("properties", {}) == {}
        assert schemas[name].get("required", []) == []


EXPECTED_INPUT_TYPES = {
    "guard_id": {"type": "string"},
    "version": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
}


def assert_evidence_schema_carries_the_contract(schema: dict) -> None:
    """발행 스키마가 담아야 할 것: 필수 표기 + 설명 + 기계가 읽는 타입."""
    assert schema["required"] == ["guard_id"]
    properties = schema["properties"]
    assert set(properties) == set(EXPECTED_INPUT_TYPES)
    assert properties["guard_id"]["type"] == EXPECTED_INPUT_TYPES["guard_id"]["type"]
    assert properties["version"]["anyOf"] == EXPECTED_INPUT_TYPES["version"]["anyOf"]
    assert properties["guard_id"]["description"] == mcp_server.GUARD_ID_INPUT
    assert properties["version"]["description"] == mcp_server.VERSION_INPUT
    assert "default" not in properties["guard_id"]
    assert properties["version"]["default"] is None


def test_tools_list_publishes_machine_readable_argument_types(tmp_path):
    """느슨한 `Any` 선언이 지운 JSON-Schema 타입을 발행 스키마가 되싣는다.

    타입이 없으면 클라이언트(특히 LLM)는 한국어 설명문으로만 인자 타입을 추측해야 한다.
    """
    schemas = tool_input_schemas(build_server(planted_store(tmp_path / "store"), now=NOW))
    assert_evidence_schema_carries_the_contract(schemas[GUARD_EVIDENCE_TOOL])


def test_stdio_round_trip_publishes_the_same_schema(tmp_path):
    """실제 stdio 하위 프로세스 핸드셰이크에서도 같은 계약이 실린다."""
    root = tmp_path / "store"
    planted_store(root)
    schemas = stdio_tool_input_schemas(root, tmp_path)
    assert set(schemas) == {LIST_GUARDS_TOOL, GUARD_EVIDENCE_TOOL, GET_REPORT_TOOL}
    assert_evidence_schema_carries_the_contract(schemas[GUARD_EVIDENCE_TOOL])


def test_tools_list_schema_is_byte_stable_across_calls(tmp_path):
    """`tools/list`를 여러 번 불러도 같은 스키마다 — 덧씌우기가 누적되지 않는다."""
    server = build_server(planted_store(tmp_path / "store"), now=NOW)
    dumped = [
        json.dumps(tool_input_schemas(server), sort_keys=True, ensure_ascii=False)
        for _ in range(3)
    ]
    assert dumped[0] == dumped[1] == dumped[2]


def test_published_schema_override_does_not_mutate_the_source_schema(tmp_path):
    """덧씌우기는 원본(툴 매니저가 들고 있는 dict)을 건드리지 않는다."""
    source = {
        "type": "object",
        "properties": {
            "guard_id": {"title": "Guard Id", "description": mcp_server.GUARD_ID_INPUT},
            "version": {
                "default": None,
                "title": "Version",
                "description": mcp_server.VERSION_INPUT,
            },
        },
        "title": "guard_evidenceArguments",
    }
    frozen = json.dumps(source, sort_keys=True, ensure_ascii=False)
    patched = mcp_server.published_input_schema(
        source, mcp_server.TOOL_INPUTS[GUARD_EVIDENCE_TOOL]
    )
    assert json.dumps(source, sort_keys=True, ensure_ascii=False) == frozen
    assert_evidence_schema_carries_the_contract(patched)


# --- 인자 선언은 한 곳뿐 (발행 스키마 ↔ 본문 검증 표류 방지) ---------------------


def test_every_published_argument_comes_from_the_single_declaration(tmp_path):
    """발행되는 인자 집합과 필수 표기는 선언표 하나에서만 나온다.

    표에 등록하지 않은 인자를 가진 도구를 새로 붙이면 여기서 깨진다 — 조용히
    `required: []`로 나가던 표류 경로를 막는다.
    """
    schemas = tool_input_schemas(build_server(planted_store(tmp_path / "store"), now=NOW))
    assert set(mcp_server.TOOL_INPUTS) <= set(schemas), "표에만 있고 서버에 없는 도구가 있다"
    for name, schema in schemas.items():
        declared = mcp_server.TOOL_INPUTS.get(name, ())
        assert set(schema.get("properties", {})) == {spec.name for spec in declared}, name
        assert schema.get("required", []) == sorted(
            spec.name for spec in declared if spec.required
        ), name


def test_declared_required_arguments_are_enforced_by_the_tool_body(tmp_path):
    """표가 필수라고 한 인자는 도구 본문이 실제로 막는다 (정화된 한 줄 오류)."""
    server = build_server(planted_store(tmp_path / "store"), now=NOW)
    checked = 0
    for name, inputs in mcp_server.TOOL_INPUTS.items():
        required = [spec.name for spec in inputs if spec.required]
        if not required:
            continue
        result = call_tool(server, name, {})
        text = result_text(result)
        assert result.is_error is True, name
        assert "\n" not in text, name
        assert required[0] in text, name
        checked += 1
    assert checked, "필수 인자를 가진 도구가 하나도 없다 — 표가 비었다"


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
    # 매핑을 객체 밖으로 건네주는 접근자가 없어야 한다 (불변식: 어디에도 저장 금지).
    assert not hasattr(boundary, "aliases")


def test_alias_substitution_is_single_pass(tmp_path):
    """치환은 원본 한 번만 훑는다 — 앞선 치환 결과를 다시 치환하지 않는다."""
    boundary = OutputBoundary(
        session_ids=("claude:sess-LONG-0001", "S1"), home="/nonexistent-home"
    )
    assert boundary.sanitize({"a": "event in claude:sess-LONG-0001"}) == {"a": "event in S1"}


def test_sanitize_recurses_into_sets(tmp_path):
    """집합도 재귀 대상이다 — 새 필드가 늘어도 기본이 안전해야 한다."""
    boundary = OutputBoundary(session_ids=(RAW_SESSION_A,), home=HOME)
    out = boundary.sanitize({"s": {f"{HOME}/a", RAW_SESSION_A}})
    assert isinstance(out["s"], list)
    assert sorted(out["s"]) == sorted(["~/a", "S1"])


def test_sanitize_fails_closed_on_an_unrecognized_type(tmp_path):
    """모르는 타입은 조용히 통과시키지 않고 막는다."""
    boundary = OutputBoundary(session_ids=(), home="/nonexistent-home")
    with pytest.raises(TypeError):
        boundary.sanitize({"when": datetime(2026, 1, 1, tzinfo=timezone.utc)})


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


def two_version_script_store(root: Path, script: Path) -> AppendStore:
    ref = EnforcementRef(script_path=str(script), file_hash="sha256:" + "c" * 64)
    store = AppendStore(root)
    store.append(
        make_spec(guard_id="script-guard", version=1, created_at=ts(0), enforcement_ref=ref)
    )
    store.append(
        make_spec(
            guard_id="script-guard",
            version=2,
            purpose="개정된 목적",
            created_at=ts(1),
            enforcement_ref=ref,
        )
    )
    return store


def test_guard_script_is_hashed_once_per_call_for_the_latest_version(tmp_path, monkeypatch):
    """가드 뷰가 이미 최신 버전을 대조했다 — 같은 응답 안에서 다시 읽지 않는다."""
    script = tmp_path / "guard.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    store = two_version_script_store(tmp_path / "store", script)

    reads: list[str] = []
    real = decision_module.enforcement_ref_for

    def counting(script_path):
        reads.append(str(script_path))
        return real(script_path)

    monkeypatch.setattr(decision_module, "enforcement_ref_for", counting)

    render_guard_evidence(store, "script-guard")
    assert len(reads) == 1, "version 생략 = 최신 버전 — 뷰의 대조 결과를 재사용해야 한다"

    reads.clear()
    render_guard_evidence(store, "script-guard", version=2)
    assert len(reads) == 1, "명시한 버전이 최신이면 마찬가지로 재사용한다"

    reads.clear()
    render_guard_evidence(store, "script-guard", version=1)
    assert len(reads) == 2, "옛 버전을 요청할 때만 그 spec을 따로 대조한다"


@pytest.mark.skipif(
    getattr(os, "geteuid", lambda: 1)() == 0, reason="root는 chmod 000 파일도 읽는다"
)
def test_unreadable_guard_script_is_unverifiable_not_a_failed_call(tmp_path):
    """§4.2 — 읽을 수 없는 구현물은 증거 전체를 날리지 않고 unverifiable + 사유다."""
    root = tmp_path / "store"
    script = tmp_path / "guard.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    store = AppendStore(root)
    spec = make_spec(
        guard_id="script-guard",
        version=1,
        created_at=ts(0),
        enforcement_ref=EnforcementRef(script_path=str(script), file_hash="sha256:" + "c" * 64),
    )
    store.append(spec)
    store.append(
        make_event(spec, event_id="ev-1", session_id="claude:s-1", occurred_at=ts(10))
    )
    script.chmod(0o000)
    try:
        payload = json.loads(render_guard_evidence(store, "script-guard"))
    finally:
        script.chmod(0o644)
    enforcement = payload["context"]["enforcement"]
    assert enforcement["status"] == "unverifiable"
    assert enforcement["reason"]
    assert "\n" not in enforcement["reason"]
    assert [row["event_id"] for row in payload["events"]] == ["ev-1"]


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


#: 문자열 version이 정수로 받아들여지는 형태 — 평범한 십진수 하나(앞뒤 공백 허용)뿐.
ACCEPTED_VERSION_STRINGS = [("2", 2), (" 2 ", 2), ("0002", 2), (2, 2)]
#: `int()`에 그대로 맡기면 통과해 버리던 형태들 — 호출자 입력을 다시 해석하는 셈이다.
REJECTED_VERSION_VALUES = ["1_0", "２", "+2", "2.0", "0x2", "", "  ", "one", "1 0", "٢"]


@pytest.mark.parametrize("value,expected", ACCEPTED_VERSION_STRINGS)
def test_version_accepts_plain_decimal_integers(tmp_path, value, expected):
    store = planted_store(tmp_path / "store")
    payload = json.loads(render_guard_evidence(store, "planted-guard", version=value))
    assert payload["context"]["version"] == expected


@pytest.mark.parametrize("value", REJECTED_VERSION_VALUES)
def test_version_rejects_everything_that_is_not_a_plain_decimal_integer(tmp_path, value):
    store = planted_store(tmp_path / "store")
    result = call_tool(
        build_server(store, now=NOW),
        GUARD_EVIDENCE_TOOL,
        {"guard_id": "planted-guard", "version": value},
    )
    text = result_text(result)
    assert result.is_error is True, value
    assert "정수여야 한다" in text, value
    assert "\n" not in text


def test_wellformed_but_absent_version_is_still_the_no_such_version_error(tmp_path):
    """F4가 바꾸는 것은 '정수인가'뿐이다 — 없는 버전은 그대로 '없는 버전' 오류다."""
    store = planted_store(tmp_path / "store")
    for value in ("-1", "77", 77):
        result = call_tool(
            build_server(store, now=NOW),
            GUARD_EVIDENCE_TOOL,
            {"guard_id": "planted-guard", "version": value},
        )
        assert result.is_error is True, value
        assert "없는 버전" in result_text(result), value


def test_overlong_digit_version_stays_inside_the_tool_error(tmp_path):
    """자릿수 한도를 넘는 십진수 문자열도 도구 밖으로 새면 안 된다.

    `int()`는 4300자리(CPython 기본)를 넘으면 `ValueError`를 던진다. 정규식만
    보고 통과시킨 뒤 변환을 감싸지 않으면 예외가 도구 밖으로 나가고, 클라이언트는
    정화 경계를 지나지 않은 SDK 문구를 받는다.
    """
    store = planted_store(tmp_path / "store")
    server = build_server(store, now=NOW)
    for digits in (4300, 4301, 5000):
        result = call_tool(
            server, GUARD_EVIDENCE_TOOL, {"guard_id": "planted-guard", "version": "9" * digits}
        )
        text = result_text(result)
        assert result.is_error is True, digits
        assert "\n" not in text, digits
        assert len(text) <= mcp_server.MAX_ECHO + 120, (digits, len(text))
        assert str(Path.home()) not in text, digits
        assert "Traceback" not in text, digits
        # 4300자리까지는 변환되어 '없는 버전', 그 위는 변환 한도에 걸려 '정수여야 한다'.
        assert ("없는 버전" in text) or ("정수여야 한다" in text), digits


def test_absent_version_error_is_bounded_like_every_other_echo(tmp_path):
    """'없는 버전' 사유도 호출자 입력을 담는다 — 상한 없이 부풀면 안 된다."""
    store = planted_store(tmp_path / "store")
    result = call_tool(
        build_server(store, now=NOW),
        GUARD_EVIDENCE_TOOL,
        {"guard_id": "planted-guard", "version": "9" * 500},
    )
    text = result_text(result)
    assert result.is_error is True
    assert "없는 버전" in text
    assert "\n" not in text
    assert len(text) <= mcp_server.MAX_ECHO + 120, len(text)


#: 기본 `strip()`이 걷어내는 유니코드 공백 — `[0-9]`로 좁힌 뜻과 어긋나므로 거부한다.
UNICODE_SPACE_VERSIONS = ["\xa02\xa0", "　2　", " 2 ", "\x1c2\x1c"]


@pytest.mark.parametrize("value", UNICODE_SPACE_VERSIONS)
def test_version_strips_ascii_whitespace_only(tmp_path, value):
    store = planted_store(tmp_path / "store")
    result = call_tool(
        build_server(store, now=NOW),
        GUARD_EVIDENCE_TOOL,
        {"guard_id": "planted-guard", "version": value},
    )
    assert result.is_error is True, repr(value)
    assert "정수여야 한다" in result_text(result), repr(value)


def test_underscore_separated_version_is_not_silently_reinterpreted(tmp_path):
    """`int("1_0")`은 10이다 — 파이썬의 관대함이 호출자 입력을 조용히 다시 읽으면 안 된다."""
    root = tmp_path / "store"
    store = AppendStore(root)
    store.append(make_spec(guard_id="guard-a", version=1, created_at=ts(0)))
    store.append(make_spec(guard_id="guard-a", version=10, purpose="개정된 목적", created_at=ts(1)))
    assert json.loads(render_guard_evidence(store, "guard-a", version=10))["context"]["version"] == 10
    with pytest.raises(ToolError) as excinfo:
        render_guard_evidence(store, "guard-a", version="1_0")
    assert "정수여야 한다" in str(excinfo.value)


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


def test_get_report_boundary_is_built_after_the_report_text(tmp_path, monkeypatch):
    """경계의 세션 목록은 보고서 본문을 만든 **뒤** 스냅샷에서 온다.

    보고서 본문은 v1 코어가 자체 `load()`로 만든다. 경계를 그보다 먼저 뜨면, 두
    적재 사이에 들어온 세션이 본문에는 있고 별칭 표에는 없어 원문 그대로 새 나간다.
    store가 append 전용이라 나중 스냅샷은 앞선 것의 상위집합이므로 순서가 곧 안전이다.
    """
    root = tmp_path / "store"
    store = simple_store(root)
    late_session = "claude:sess-LATE-9999"

    def appending_generate_report(target_store, *, now=None):
        spec = make_spec(guard_id="guard-late", version=1, created_at=ts(4))
        target_store.append(spec)
        target_store.append(
            make_event(
                spec, event_id="ev-late", session_id=late_session, occurred_at=ts(40)
            )
        )
        return f"# 보고서\n\n세션 {late_session} 사건 1건\n"

    monkeypatch.setattr(mcp_server, "generate_report", appending_generate_report)
    text = render_report_text(store, now=NOW)
    assert late_session not in text, "보고서가 본 세션이 별칭 표에 없다 — 경계를 먼저 떴다"
    assert "S1" in text


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


# --- §3 SDK 경계: v1 코어는 SDK 없이 돈다 --------------------------------------

#: 하위 프로세스에서 돌린다 — 이미 import된 SDK를 이 프로세스에서 걷어낼 수 없다.
#: 텍스트 스캔("mcp"라는 글자가 있는가)이 아니라 실제 동작으로 확인한다.
SDK_FREE_PROBE = """
import importlib
import json
import pathlib
import sys
import tempfile


class BlockMCP:
    def find_spec(self, name, path=None, target=None):
        if name == "mcp" or name.startswith("mcp."):
            raise ImportError("mcp is blocked for this probe")
        return None


for cached in [n for n in list(sys.modules) if n == "mcp" or n.startswith("mcp.")]:
    del sys.modules[cached]
sys.meta_path.insert(0, BlockMCP())

import rejectbench

package = pathlib.Path(rejectbench.__file__).resolve().parent
failed = []
for module in sorted(p.stem for p in package.glob("*.py") if p.stem != "__init__"):
    try:
        importlib.import_module("rejectbench." + module)
    except ImportError:
        failed.append(module)

# import만으로는 "돈다"의 증거가 약하다 — 임시 store로 실제 보고서를 만들어 본다.
from rejectbench.report import generate_report
from rejectbench.store import AppendStore

with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp) / "store"
    text = generate_report(AppendStore(root))
    created = root.exists()

print(json.dumps({"failed": failed, "report": text[:40], "created": created}))
"""


def test_v1_core_works_when_the_sdk_cannot_be_imported(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-c", SDK_FREE_PROBE],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(repo_root)},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["failed"] == ["mcp_server"], f"SDK 의존이 새 모듈 밖으로 샜다: {payload}"
    assert payload["report"].startswith("# Reject Bench")
    assert payload["created"] is False


# --- 실행 진입점 --------------------------------------------------------------


class _StubServer:
    def __init__(self):
        self.transport = None

    def run(self, transport: str) -> None:
        self.transport = transport


def capture_main(monkeypatch) -> dict:
    """`main()`이 만든 store 루트와 전송 방식만 잡아낸다 — 서버는 띄우지 않는다."""
    captured: dict = {}

    def fake_build_server(store, *, now=None):
        captured["root"] = store.root
        captured["server"] = _StubServer()
        return captured["server"]

    monkeypatch.setattr(mcp_server, "build_server", fake_build_server)
    return captured


def test_main_uses_the_store_argument(tmp_path, monkeypatch):
    captured = capture_main(monkeypatch)
    root = tmp_path / "store"
    assert mcp_server.main(["--store", str(root)]) == 0
    assert captured["root"] == root
    assert captured["server"].transport == "stdio"
    assert not root.exists()  # 없어도 만들지 않는다


def test_main_defaults_to_the_production_root(tmp_path, monkeypatch):
    captured = capture_main(monkeypatch)
    assert mcp_server.main([]) == 0
    # 운영 store를 읽지도 만들지도 않는다 — 구성된 경로만 대조한다.
    assert captured["root"] == production_root()
    assert captured["server"].transport == "stdio"
