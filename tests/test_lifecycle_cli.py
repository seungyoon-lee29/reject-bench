"""검토→결정 CLI 왕복 스모크 (T5).

임시 store만 사용한다 (운영 `data/` 비접촉은 conftest가 강제). 뷰는 stdout
출력뿐이고 파일로 내보내는 옵션이 없다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from rejectbench import AppendStore, Utility, Verdict
from rejectbench.cli import main
from tests.factories import make_event, make_review, make_spec, make_verdict, ts

REPO_ROOT = Path(__file__).resolve().parents[1]

MODIFY_DRAFT = {
    "guard_id": "guard-a",
    "project": "reject-bench",
    "purpose": "위험 git 명령이 이력을 파괴하는 사고 방지",
    "policy": "force push·hard reset·filter-branch를 차단한다",
    "exceptions": ["release/* 브랜치는 허용"],
    "allow_examples": ["git status"],
    "block_examples": ["git push --force"],
}


@pytest.fixture()
def store_dir(tmp_path) -> Path:
    return tmp_path / "store"


def seed_two_sessions(store_dir: Path, *, extra_pending: bool = False):
    """guard-a: 두 세션의 판정 확정 사건 ev-1·ev-2 (+선택: 판정 있는 ev-3)."""
    store = AppendStore(store_dir)
    spec = make_spec()
    store.append(spec)
    e1 = make_event(spec, event_id="ev-1", session_id="claude:s-1")
    e2 = make_event(spec, event_id="ev-2", session_id="claude:s-2", occurred_at=ts(20))
    store.append(e1)
    store.append(e2)
    store.append(make_verdict(e1, verdict_id="vd-1"))
    store.append(make_verdict(e2, verdict_id="vd-2", verdict=Verdict.INCORRECT_BLOCK))
    if extra_pending:
        e3 = make_event(spec, event_id="ev-3", session_id="claude:s-1", occurred_at=ts(30))
        store.append(e3)
        store.append(make_verdict(e3, verdict_id="vd-3"))
    return spec


def reviewed_seed(store_dir: Path):
    """CLI 없이 직접 검토까지 채운 seed — 결정 전용 테스트용."""
    spec = seed_two_sessions(store_dir)
    store = AppendStore(store_dir)
    dataset_events = {"ev-1": Utility.USEFUL, "ev-2": Utility.UNNECESSARY}
    for i, (event_id, utility) in enumerate(dataset_events.items()):
        event = make_event(spec, event_id=event_id)  # 참조용 — append하지 않는다
        store.append(
            make_review(event, review_id=f"rv-{i}", utility=utility)
        )
    return spec


class TestReviewDecisionRoundTrip:
    def test_full_flow(self, store_dir, capsys):
        spec = seed_two_sessions(store_dir, extra_pending=True)
        s = str(store_dir)

        # 1) 전수 검토 큐
        assert main(["review", "list", "--store", s]) == 0
        out = json.loads(capsys.readouterr().out)
        assert [e["event_id"] for e in out["pending"]] == ["ev-1", "ev-2", "ev-3"]

        # 2) 사건별 검토 기록
        assert (
            main(
                [
                    "review", "record", "--store", s,
                    "--event", "ev-1", "--utility", "useful", "--note", "실제 사고를 막았다",
                ]
            )
            == 0
        )
        recorded = json.loads(capsys.readouterr().out)
        assert recorded["utility"] == "useful"
        assert (
            main(["review", "record", "--store", s, "--event", "ev-2", "--utility", "unnecessary"])
            == 0
        )
        capsys.readouterr()

        # 3) 검토 중 시험 발동 확인 → test 강등 (사유 필수)
        assert (
            main(["review", "demote", "--store", s, "--event", "ev-3", "--reason", "강제 발동 확인"])
            == 0
        )
        demoted = json.loads(capsys.readouterr().out)
        assert demoted["new_value"] == "test"

        # 4) 큐가 빈다 — 강등 사건은 test로 집계된다
        assert main(["review", "list", "--store", s]) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["pending"] == []
        assert out["counts"]["test"] == 1

        # 5) 가드별 최소 뷰 — 세션 수·사건·두 판단 축·판정 가능 상태
        assert main(["guard", "show", "guard-a", "--store", s]) == 0
        text = capsys.readouterr().out
        for token in ("guard-a", "ev-1", "correct_block", "useful", "세션", "판정 가능"):
            assert token in text

        # 6) keep 결정 + 근거 연결
        assert (
            main(
                [
                    "decide", "--store", s, "--guard", "guard-a", "--decision", "keep",
                    "--evidence", "ev-1", "--evidence", "ev-2",
                    "--rationale", "두 세션 근거로 유지",
                ]
            )
            == 0
        )
        decided = json.loads(capsys.readouterr().out)
        assert decided["countable"] is True
        assert decided["evidence_event_ids"] == ["ev-1", "ev-2"]

        # 7) 결정 변경 — 덮지 않고 새 레코드 append, 이력 조회 가능
        assert (
            main(
                [
                    "decide", "--store", s, "--guard", "guard-a", "--decision", "remove",
                    "--evidence", "ev-1", "--evidence", "ev-2",
                    "--rationale", "불필요 판단 우세로 번복",
                ]
            )
            == 0
        )
        capsys.readouterr()
        assert main(["decisions", "--store", s, "--guard", "guard-a"]) == 0
        history = json.loads(capsys.readouterr().out)
        assert [h["decision"] for h in history] == ["keep", "remove"]
        assert history[0]["decision_id"] != history[1]["decision_id"]

        # 8) remove 뒤 같은 가드 발동 → 뷰에 post-remove 표시
        AppendStore(store_dir).append(
            make_event(spec, event_id="ev-9", session_id="claude:s-9", occurred_at=ts(300))
        )
        assert main(["guard", "show", "guard-a", "--store", s]) == 0
        assert "post-remove" in capsys.readouterr().out


class TestModifyCli:
    def test_modify_requires_draft_file(self, store_dir, capsys):
        reviewed_seed(store_dir)
        assert (
            main(
                [
                    "decide", "--store", str(store_dir), "--guard", "guard-a",
                    "--decision", "modify",
                    "--evidence", "ev-1", "--evidence", "ev-2",
                    "--rationale", "draft 없는 modify",
                ]
            )
            == 1
        )
        assert "오류" in capsys.readouterr().err

    def test_modify_creates_version_and_checks_enforcement(self, store_dir, tmp_path, capsys):
        reviewed_seed(store_dir)
        script = tmp_path / "guard.sh"
        script.write_text("#!/bin/sh\nexit 2\n", encoding="utf-8")
        draft_path = tmp_path / "draft.json"
        draft_path.write_text(json.dumps(MODIFY_DRAFT, ensure_ascii=False), encoding="utf-8")

        assert (
            main(
                [
                    "decide", "--store", str(store_dir), "--guard", "guard-a",
                    "--decision", "modify",
                    "--evidence", "ev-1", "--evidence", "ev-2",
                    "--rationale", "예외 추가",
                    "--modify-file", str(draft_path),
                    "--enforcement-script", str(script),
                ]
            )
            == 0
        )
        payload = json.loads(capsys.readouterr().out)
        assert payload["decision"] == "modify"
        assert payload["resulting_guard_version"] == 2
        assert payload["enforcement"]["status"] == "in_sync"
        assert payload["countable"] is True

        # 뷰에 새 버전과 구현물 대조 상태가 나온다
        assert main(["guard", "show", "guard-a", "--store", str(store_dir)]) == 0
        text = capsys.readouterr().out
        assert "v2" in text
        assert "in_sync" in text

    def test_modify_draft_guard_mismatch_rejected(self, store_dir, tmp_path, capsys):
        reviewed_seed(store_dir)
        draft_path = tmp_path / "draft.json"
        draft_path.write_text(
            json.dumps({**MODIFY_DRAFT, "guard_id": "guard-b"}, ensure_ascii=False),
            encoding="utf-8",
        )
        assert (
            main(
                [
                    "decide", "--store", str(store_dir), "--guard", "guard-a",
                    "--decision", "modify",
                    "--evidence", "ev-1", "--evidence", "ev-2",
                    "--rationale", "가드 불일치",
                    "--modify-file", str(draft_path),
                ]
            )
            == 1
        )
        assert "오류" in capsys.readouterr().err


class TestNoFilterEscape:
    def test_review_list_rejects_filter_flags(self, store_dir):
        # 큐에서 사건을 선별해 빼는 CLI 경로가 없어야 한다.
        with pytest.raises(SystemExit) as exc:
            main(["review", "list", "--store", str(store_dir), "--exclude", "ev-1"])
        assert exc.value.code == 2


class TestErrorPaths:
    def test_review_record_unknown_event(self, store_dir, capsys):
        seed_two_sessions(store_dir)
        assert (
            main(["review", "record", "--store", str(store_dir), "--event", "ev-없음", "--utility", "useful"])
            == 1
        )
        assert "오류" in capsys.readouterr().err

    def test_review_record_bad_utility_choice(self, store_dir):
        with pytest.raises(SystemExit) as exc:
            main(["review", "record", "--store", str(store_dir), "--event", "ev-1", "--utility", "great"])
        assert exc.value.code == 2

    def test_review_demote_requires_reason(self, store_dir):
        with pytest.raises(SystemExit) as exc:
            main(["review", "demote", "--store", str(store_dir), "--event", "ev-1"])
        assert exc.value.code == 2

    def test_decide_unknown_guard(self, store_dir, capsys):
        assert (
            main(
                [
                    "decide", "--store", str(store_dir), "--guard", "guard-없음",
                    "--decision", "keep", "--rationale", "r",
                ]
            )
            == 1
        )
        assert "오류" in capsys.readouterr().err

    def test_decide_unreviewed_evidence(self, store_dir, capsys):
        seed_two_sessions(store_dir)  # 판정만 있고 검토가 없다
        assert (
            main(
                [
                    "decide", "--store", str(store_dir), "--guard", "guard-a",
                    "--decision", "keep",
                    "--evidence", "ev-1", "--evidence", "ev-2",
                    "--rationale", "검토 없는 근거",
                ]
            )
            == 1
        )
        assert "오류" in capsys.readouterr().err

    def test_guard_show_unknown_guard(self, store_dir, capsys):
        assert main(["guard", "show", "guard-없음", "--store", str(store_dir)]) == 1
        assert "오류" in capsys.readouterr().err


class TestSubprocessSmoke:
    def test_module_entrypoint_review_list(self, store_dir):
        seed_two_sessions(store_dir)
        proc = subprocess.run(
            [sys.executable, "-m", "rejectbench.cli", "review", "list", "--store", str(store_dir)],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        assert payload["counts"]["pending"] == 2
