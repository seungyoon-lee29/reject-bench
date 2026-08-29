"""기술 E2E (T6, spec §7 "기술 E2E").

한 테스트 안에서 명시적 시험 가드로 전 흐름을 실행한다:
등록 → 발동 기록(래퍼 + mock 가드, `REJECTBENCH_TEST_SESSION`) → 판정(승인·
fake 전송, 시험 사건은 운영 판정 대상이 아님을 실기록으로 확인) → 검토 →
결정(시험 근거는 게이트가 거부) → 보고서 재생성.

- 보고서에 `기술 검증용 test evidence` 표시가 박히고 운영 지표에 시험 사건이
  0건 포함된다.
- 네트워크 비접촉: 전송 계층은 monkeypatch된 fake뿐이고, 시험 사건 경로에서는
  전송 계층이 생성조차 되지 않아야 한다.
- 임시 store만 사용한다 (운영 `data/` 비접촉은 conftest가 강제).

같은 파일의 운영 흐름 E2E는 플래그 없는 발동(operation)으로 판정→검토→결정
→보고서가 실제 원수(1/1)를 내는 경로를 fake 전송으로 검증한다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from rejectbench import AppendStore, GuardEvent, Origin, OriginEvidence
from rejectbench.cli import main
from rejectbench.judge import BILLING_ENV, CALIBRATION_FILENAME
from rejectbench.report import REPORTS_DIRNAME, TEST_EVIDENCE_MARK

REPO_ROOT = Path(__file__).resolve().parents[1]

MOCK_GUARD = """#!/bin/bash
cat > /dev/null
echo "BLOCKED: 'git push --force origin main' matches guard-e2e policy" >&2
exit 2
"""

DRAFT = {
    "guard_id": "guard-e2e",
    "project": "reject-bench",
    "purpose": "기술 E2E 검증용 시험 가드",
    "policy": "force push를 차단한다",
    "exceptions": [],
    "allow_examples": ["git status"],
    "block_examples": ["git push --force origin main"],
}


class ForbiddenTransport:
    """시험 사건 경로에서는 전송 계층이 생성조차 되면 안 된다."""

    def __init__(self, *args, **kwargs):
        raise AssertionError("시험 사건만 있는 store에서 전송 계층이 생성됐다")


class SmartFakeTransport:
    """fake 전송 — 교정 예시에는 기대 판정을, 사건에는 correct_block을 낸다."""

    calls: int = 0

    def __init__(self, *args, **kwargs):
        pass

    def complete(self, *, model_id: str, messages: list[dict], settings: dict) -> str:
        SmartFakeTransport.calls += 1
        content = messages[1]["content"]
        if '"blocked_action":"git status"' in content:
            verdict = "incorrect_block"
        else:
            verdict = "correct_block"
        return json.dumps({"verdict": verdict, "reason": "정책 조항 대조"}, ensure_ascii=False)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv(BILLING_ENV, raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("REJECTBENCH_TEST_SESSION", raising=False)


def write_mock_guard(tmp_path: Path) -> Path:
    script = tmp_path / "mock-guard-e2e.sh"
    script.write_text(MOCK_GUARD, encoding="utf-8")
    script.chmod(0o755)
    return script


def write_draft(tmp_path: Path) -> Path:
    path = tmp_path / "draft-e2e.json"
    path.write_text(json.dumps(DRAFT, ensure_ascii=False), encoding="utf-8")
    return path


def payload(session_id: str, cwd: str) -> str:
    return json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "session_id": session_id,
            "cwd": cwd,
            "tool_name": "Bash",
            "tool_input": {"command": "git push --force origin main"},
        },
        ensure_ascii=False,
    )


def run_wrapper(
    guard: Path, stdin_text: str, store_root: Path, fallback: Path, *, test_flag: bool
) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("REJECTBENCH_")}
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["REJECTBENCH_STORE"] = str(store_root)
    env["REJECTBENCH_FALLBACK_DIR"] = str(fallback)
    if test_flag:
        env["REJECTBENCH_TEST_SESSION"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "rejectbench.wrapper", "--guard", str(guard)],
        input=stdin_text.encode("utf-8"),
        capture_output=True,
        env=env,
        cwd=str(REPO_ROOT),
        timeout=60,
    )


def stored_events(store_root: Path) -> list[GuardEvent]:
    return [
        record
        for record in AppendStore(store_root).load().records
        if isinstance(record, GuardEvent)
    ]


def assert_no_private_leak(report: str, store_root: Path, session_ids: list[str]) -> None:
    assert str(Path.home()) not in report
    assert "/Users/" not in report
    assert str(store_root) not in report
    assert "claude:" not in report
    for session_id in session_ids:
        assert session_id not in report


class TestTechnicalE2E:
    def test_full_flow_with_test_flag(self, tmp_path, monkeypatch, capsys):
        store_root = tmp_path / "store"
        fallback = tmp_path / "fallback"
        fallback.mkdir()
        guard = write_mock_guard(tmp_path)
        s = str(store_root)

        # 1) 등록 — 명시적 시험 가드 (enforcement_ref로 래퍼 사건이 spec에 연결된다)
        assert main(
            ["register", "--store", s, "--file", str(write_draft(tmp_path)),
             "--enforcement-script", str(guard)]
        ) == 0
        registered = json.loads(capsys.readouterr().out)
        assert registered["guard_id"] == "guard-e2e" and registered["version"] == 1

        # 2) 발동 기록 — 래퍼 + mock 가드, 시험 세션 플래그, 서로 다른 두 세션
        cwd = str(tmp_path / "proj-e2e")
        for session_id in ("e2e-s1", "e2e-s2"):
            proc = run_wrapper(
                guard, payload(session_id, cwd), store_root, fallback, test_flag=True
            )
            assert proc.returncode == 2  # 가드 결과 투명 전달
            assert b"BLOCKED" in proc.stderr
        events = stored_events(store_root)
        assert len(events) == 2
        for event in events:
            assert event.origin is Origin.TEST
            assert event.origin_evidence is OriginEvidence.EXPLICIT_FLAG
            assert event.guard_id == "guard-e2e" and not event.unregistered
            assert "<command omitted>" in event.reason  # 명령 전문 비저장

        # 3) 판정 — 승인했지만 시험 사건은 운영 판정 대상이 아니다: 호출 0건,
        #    전송 계층 생성 자체가 없어야 한다 (네트워크 비접촉).
        monkeypatch.setattr("rejectbench.judge.OpenAITransport", ForbiddenTransport)
        assert main(["judge", "--store", s, "--approve-billing"]) == 0
        judged = json.loads(capsys.readouterr().out)
        assert judged["approved"] is True
        assert judged["excluded"]["test"] == 2
        assert judged["planned_llm_calls"] == 0
        assert judged["judged"] == []

        # 4) 검토 — 전수 검토 큐는 운영 사건 대상이라 비어 있고, 시험 사건의
        #    검토 기록 자체는 가능하다 (지표 밖).
        assert main(["review", "list", "--store", s]) == 0
        queue = json.loads(capsys.readouterr().out)
        assert queue["pending"] == [] and queue["counts"]["test"] == 2
        assert main(
            ["review", "record", "--store", s, "--event", events[0].event_id,
             "--utility", "useful", "--note", "기술 검증용 검토"]
        ) == 0
        capsys.readouterr()

        # 5) 결정 — 시험 사건은 근거 부적격: 게이트가 거부한다.
        assert main(
            ["decide", "--store", s, "--guard", "guard-e2e", "--decision", "keep",
             "--evidence", events[0].event_id, "--evidence", events[1].event_id,
             "--rationale", "시험 근거로는 결정 불가 검증"]
        ) == 1
        assert "부적격" in capsys.readouterr().err

        # 6) 보고서 재생성 — stdout과 실제 파일 산출
        assert main(["report", "--store", s]) == 0
        report = capsys.readouterr().out
        assert TEST_EVIDENCE_MARK in report
        assert "test evidence only" in report
        assert "운영: 미검증" in report  # 시험 사건만으로는 가치 미검증
        assert "미검증 (분모 0 — 성공 아님)" in report
        assert "operation 0 · test 2 · unknown 0 · unregistered 0" in report
        assert "시험(test) 사건: 2건" in report
        assert "guard-e2e" in report
        assert_no_private_leak(report, store_root, ["e2e-s1", "e2e-s2"])

        assert main(["report", "--store", s, "--out"]) == 0
        written = Path(json.loads(capsys.readouterr().out)["written"])
        assert written.parent == store_root / REPORTS_DIRNAME
        content = written.read_text(encoding="utf-8")
        assert TEST_EVIDENCE_MARK in content
        assert "운영: 미검증" in content
        assert_no_private_leak(content, store_root, ["e2e-s1", "e2e-s2"])


class TestOperationFlowE2E:
    def test_full_flow_operation_events(self, tmp_path, monkeypatch, capsys):
        store_root = tmp_path / "store"
        fallback = tmp_path / "fallback"
        fallback.mkdir()
        guard = write_mock_guard(tmp_path)
        s = str(store_root)

        assert main(
            ["register", "--store", s, "--file", str(write_draft(tmp_path)),
             "--enforcement-script", str(guard)]
        ) == 0
        capsys.readouterr()

        # 플래그 없는 발동 — operation + default_inherited, 서로 다른 두 세션
        cwd = str(tmp_path / "proj-e2e")
        for session_id in ("e2e-b1", "e2e-b2"):
            proc = run_wrapper(
                guard, payload(session_id, cwd), store_root, fallback, test_flag=False
            )
            assert proc.returncode == 2
        events = stored_events(store_root)
        assert [e.origin for e in events] == [Origin.OPERATION, Origin.OPERATION]

        # 판정 — fake 전송으로 교정(2건)과 사건 판정(2건) 실행
        SmartFakeTransport.calls = 0
        monkeypatch.setattr("rejectbench.judge.OpenAITransport", SmartFakeTransport)
        assert main(["judge", "--store", s, "--approve-billing"]) == 0
        judged = json.loads(capsys.readouterr().out)
        assert judged["planned_llm_calls"] == 4
        assert SmartFakeTransport.calls == 4
        assert [j["verdict"] for j in judged["judged"]] == ["correct_block"] * 2
        assert judged["calibrations"] == [
            {"guard_id": "guard-e2e", "guard_version": 1, "passed": True, "reused": False}
        ]
        assert (store_root / CALIBRATION_FILENAME).exists()

        # 검토 — 전수 큐에 2건, 각각 useful 기록
        assert main(["review", "list", "--store", s]) == 0
        queue = json.loads(capsys.readouterr().out)
        assert [e["event_id"] for e in queue["pending"]] == [e.event_id for e in events]
        for event in events:
            assert main(
                ["review", "record", "--store", s, "--event", event.event_id,
                 "--utility", "useful", "--note", "실제로 사고를 막았다"]
            ) == 0
            capsys.readouterr()

        # 결정 — 두 세션 근거 keep, 가치 검증 산입
        assert main(
            ["decide", "--store", s, "--guard", "guard-e2e", "--decision", "keep",
             "--evidence", events[0].event_id, "--evidence", events[1].event_id,
             "--rationale", "두 세션 근거로 유지"]
        ) == 0
        decided = json.loads(capsys.readouterr().out)
        assert decided["countable"] is True

        # 보고서 — 실제 원수 병기와 교정 상태
        assert main(["report", "--store", s]) == 0
        report = capsys.readouterr().out
        assert "1/1 (100.0%)" in report  # 증거 기반 결정 완료율
        assert "운영: 미검증" not in report
        assert report.count("0/2 (0.0%)") >= 3  # 진단 지표 3종 모두 원수 병기
        assert "operation 2 · test 0 · unknown 0 · unregistered 0" in report
        assert "guard-e2e v1" in report
        assert "통과 (2/2)" in report
        assert TEST_EVIDENCE_MARK in report  # 기술 절은 항상 존재 (시험 0건)
        assert "시험(test) 사건: 0건" in report
        assert_no_private_leak(report, store_root, ["e2e-b1", "e2e-b2"])
