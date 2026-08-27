"""접두 해시 baseline — append는 통과, 기존 줄 변경은 잡힌다."""

from __future__ import annotations

import hashlib
import json

import pytest

from rejectbench import v0_baseline
from rejectbench.v0_baseline import capture, complete_prefix, measure, verify

CAPTURED_AT = "2026-08-27T00:00:00Z"

THREE_LINES = b'{"a": 1}\n{"a": 2}\n{"a": 3}\n'


@pytest.fixture
def log(isolated_store):
    path = isolated_store / "events.jsonl"
    path.write_bytes(THREE_LINES)
    return path


def test_접두는_마지막_개행까지만_잡는다():
    """append 중 잘린 마지막 행은 baseline에 들어가지 않는다."""
    assert complete_prefix(THREE_LINES + b'{"a": 4') == THREE_LINES


def test_개행이_없으면_접두는_비어_있다():
    assert complete_prefix(b'{"a": 1') == b""


def test_빈_로그도_캡처된다(isolated_store):
    empty = isolated_store / "events.jsonl"
    empty.write_bytes(b"")
    record = capture(empty, CAPTURED_AT)
    assert record["prefix_lines"] == 0
    assert record["sha256"] == hashlib.sha256(b"").hexdigest()


def test_캡처는_행수와_해시를_기록한다(log):
    record = capture(log, CAPTURED_AT)
    assert record["prefix_lines"] == 3
    assert record["prefix_bytes"] == len(THREE_LINES)
    assert record["sha256"] == hashlib.sha256(THREE_LINES).hexdigest()
    assert record["captured_at"] == CAPTURED_AT


def test_뒤에_붙는_줄은_검사하지_않는다(log):
    baseline = capture(log, CAPTURED_AT)
    with log.open("ab") as handle:
        handle.write(b'{"a": 4}\n{"a": 5}\n')

    ok, message = verify(baseline, log)
    assert ok
    assert "+2행" in message


def test_잘린_마지막_행이_붙어_있어도_통과한다(log):
    """훅이 쓰는 도중에 검사해도 깨지지 않아야 한다."""
    baseline = capture(log, CAPTURED_AT)
    with log.open("ab") as handle:
        handle.write(b'{"a": 4')

    ok, _ = verify(baseline, log)
    assert ok


def test_기존_줄을_고치면_잡힌다(log):
    baseline = capture(log, CAPTURED_AT)
    log.write_bytes(THREE_LINES.replace(b'"a": 2', b'"a": 9'))

    ok, message = verify(baseline, log)
    assert not ok
    assert "접두 해시 불일치" in message


def test_기존_줄을_지우면_잡힌다(log):
    baseline = capture(log, CAPTURED_AT)
    log.write_bytes(b'{"a": 1}\n{"a": 3}\n')

    ok, message = verify(baseline, log)
    assert not ok
    assert "짧다" in message


def test_같은_길이로_바꿔치기해도_잡힌다(log):
    """길이 검사만으로는 안 되고 해시가 필요한 경우."""
    baseline = capture(log, CAPTURED_AT)
    swapped = b'{"a": 3}\n{"a": 2}\n{"a": 1}\n'
    assert len(swapped) == baseline["prefix_bytes"]
    log.write_bytes(swapped)

    ok, message = verify(baseline, log)
    assert not ok
    assert "접두 해시 불일치" in message


def test_측정은_접두에만_걸린다():
    assert measure(THREE_LINES + b'{"a": 4') == measure(THREE_LINES)


@pytest.fixture
def cli(log, tmp_path, monkeypatch):
    """CLI가 실제 baseline 파일 대신 임시 경로를 쓰게 한다."""
    target = tmp_path / "baseline.json"
    monkeypatch.setattr(v0_baseline, "BASELINE_PATH", target)
    return target


def test_CLI가_캡처하고_검사한다(cli):
    assert v0_baseline.main(["capture"]) == 0
    assert json.loads(cli.read_text(encoding="utf-8"))["prefix_lines"] == 3
    assert v0_baseline.main(["verify"]) == 0


def test_CLI는_기존_baseline을_덮어쓰지_않는다(cli, log):
    """T5 자기검증 방지 — 작업 뒤 baseline을 다시 뜨는 경로를 막는다."""
    assert v0_baseline.main(["capture"]) == 0
    before = cli.read_text(encoding="utf-8")

    log.write_bytes(b'{"a": 9}\n')
    assert v0_baseline.main(["capture"]) == 2
    assert cli.read_text(encoding="utf-8") == before


def test_CLI는_불일치에_1을_돌려준다(cli, log):
    assert v0_baseline.main(["capture"]) == 0
    log.write_bytes(THREE_LINES.replace(b'"a": 2', b'"a": 9'))
    assert v0_baseline.main(["verify"]) == 1


def test_CLI는_로그가_없으면_2를_돌려준다(cli, log):
    log.unlink()
    assert v0_baseline.main(["capture"]) == 2
    assert v0_baseline.main(["verify"]) == 2
