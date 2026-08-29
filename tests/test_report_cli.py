"""report 서브커맨드 스모크 (T6).

stdout이 기본, `--out`은 파일 산출이다. `--out`만 주면 store 루트 하위
`reports/report-<UTC시각>.md` 기본 경로를 쓴다. 임시 store만 사용한다
(운영 `data/` 비접촉은 conftest가 강제).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from rejectbench import AppendStore
from rejectbench.cli import main
from rejectbench.report import REPORTS_DIRNAME, TEST_EVIDENCE_MARK
from tests.factories import make_event, make_spec


@pytest.fixture()
def store_dir(tmp_path) -> Path:
    return tmp_path / "store"


def seed(store_dir: Path) -> None:
    store = AppendStore(store_dir)
    spec = make_spec()
    store.append(spec)
    store.append(make_event(spec, event_id="ev-1", session_id="claude:s-1"))


class TestReportCli:
    def test_stdout_markdown(self, store_dir, capsys):
        seed(store_dir)
        assert main(["report", "--store", str(store_dir)]) == 0
        out = capsys.readouterr().out
        assert out.startswith("# Reject Bench 보고서")
        assert TEST_EVIDENCE_MARK in out

    def test_bare_out_writes_default_path(self, store_dir, capsys):
        seed(store_dir)
        assert main(["report", "--store", str(store_dir), "--out"]) == 0
        payload = json.loads(capsys.readouterr().out)
        written = Path(payload["written"])
        assert written.parent == store_dir / REPORTS_DIRNAME
        assert re.fullmatch(r"report-\d{8}T\d{6}Z\.md", written.name)
        content = written.read_text(encoding="utf-8")
        assert content.startswith("# Reject Bench 보고서")
        assert TEST_EVIDENCE_MARK in content

    def test_out_custom_path(self, store_dir, tmp_path, capsys):
        seed(store_dir)
        target = tmp_path / "nested" / "custom-report.md"
        assert main(["report", "--store", str(store_dir), "--out", str(target)]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert Path(payload["written"]) == target
        assert TEST_EVIDENCE_MARK in target.read_text(encoding="utf-8")

    def test_report_survives_corrupt_lines(self, store_dir, capsys):
        seed(store_dir)
        with open(store_dir / "records.jsonl", "a", encoding="utf-8") as fh:
            fh.write("{broken\n")
        assert main(["report", "--store", str(store_dir)]) == 0
        out = capsys.readouterr().out
        assert "손상" in out
