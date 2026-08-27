"""후보 규칙과 규칙 버전.

여기 쓰인 마커 문자열은 전부 합성이다. 실물 거부 사유는 아직 원장에 오르지
않았고, 추적 경로인 테스트 파일에 먼저 실으면 원장 밖으로 새어 나간다.
"""

from __future__ import annotations

import re
import subprocess

import pytest

from rejectbench import rules
from rejectbench.rules import (
    CANDIDATE_MARKERS,
    EXCLUSION_CODES,
    QUEUE_DECISIONS,
    RULE_PATHS,
    candidate_markers,
    is_candidate,
    rules_version,
)


# ── 후보 규칙 ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Exit code 1\n거부: `합성-가드` 은 …", "M1"),
        ("`--합성` 는 지금 돌릴 수 없다: …", "M2"),
        ("`--합성` 은 지금 돌릴 수 없다: …", "M3"),
        ("가드: 거부 ✅", "M4"),
        ("REJECT-BENCH/1 {…}", "M5"),
    ],
)
def test_마커가_후보를_고른다(text, expected):
    assert expected in candidate_markers(text)
    assert is_candidate(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "ls: no such file or directory",
        "grep: 패턴을 찾지 못했다",
        "Exit code 1\nTraceback (most recent call last):",
    ],
)
def test_마커가_없으면_후보가_아니다(text):
    assert candidate_markers(text) == ()
    assert not is_candidate(text)


def test_마커가_여러_개면_전부_돌려준다():
    text = "거부: 어쩌고\n가드: 거부 ✅"
    assert set(candidate_markers(text)) == {"M1", "M4"}


def test_마커_코드는_유일하다():
    codes = [m.code for m in CANDIDATE_MARKERS]
    assert len(codes) == len(set(codes))


def test_로그를_보고_만든_마커는_표기돼_있다():
    """M4는 데이터에서 나온 규칙이다 — 선등록으로 읽히면 안 된다."""
    post_hoc = {m.code for m in CANDIDATE_MARKERS if m.post_hoc}
    assert post_hoc == {"M4"}
    for marker in CANDIDATE_MARKERS:
        assert marker.source
        assert marker.note


def test_배제는_4종이고_큐_선택지는_3종():
    assert set(EXCLUSION_CODES) == {"a", "b", "c", "d"}
    assert QUEUE_DECISIONS == ("이관", "탈락", "중복")


# ── 규칙 버전 ───────────────────────────────────────────────────────────────


def _fake_run(stdout="", returncode=0):
    def run(*args, **kwargs):
        del args, kwargs
        return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")

    return run


def test_규칙_버전은_커밋_해시(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run("a" * 40 + "\n"))
    assert rules_version() == "a" * 40


def test_이력이_없으면_None(monkeypatch):
    """규칙 파일이 아직 커밋되지 않은 상태 — 임의로 채우면 '미상'이 사라진다."""
    monkeypatch.setattr(subprocess, "run", _fake_run(""))
    assert rules_version() is None


def test_git이_실패하면_None(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run("무언가", returncode=128))
    assert rules_version() is None


@pytest.mark.parametrize("boom", [OSError("git 없음"), subprocess.TimeoutExpired("git", 10)])
def test_git이_없거나_멈추면_None(monkeypatch, boom):
    def run(*args, **kwargs):
        del args, kwargs
        raise boom

    monkeypatch.setattr(subprocess, "run", run)
    assert rules_version() is None


def test_조회_대상_경로가_고정돼_있다():
    assert RULE_PATHS == ("docs/형식-표준.md", "rejectbench/rules.py")


def test_실제_조회는_해시_아니면_None():
    version = rules_version()
    assert version is None or re.fullmatch(r"[0-9a-f]{40}", version)


def test_조회는_읽기만_한다(monkeypatch):
    """불변식 7 — 상태를 바꾸는 git 명령을 쓰지 않는다."""
    seen = {}

    def run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    rules.rules_version()
    assert seen["cmd"][:3] == ["git", "log", "-1"]
