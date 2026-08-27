"""거부 출력 형식 v1 파서 — 정상·위반·경계.

정본은 `docs/형식-표준.md`. 여기서 검사하는 것은 그 문서의 조항들이지
구현의 편의가 아니다.
"""

from __future__ import annotations

import json

import pytest

from rejectbench.format import CONFORMING, RAW, parse_line, parse_text


def emit(payload: dict | str, version: int | str = 1) -> str:
    body = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return f"REJECT-BENCH/{version} {body}"


MINIMAL = {"guard": "evaluation-live-waste", "reason": "라이브 산출물을 덮어쓴다."}


# ── 정상 ────────────────────────────────────────────────────────────────────


def test_최소_필드로_준수한다():
    found = parse_line(emit(MINIMAL))
    assert found is not None
    assert found.conforming
    assert found.origin == CONFORMING
    assert found.fields == MINIMAL
    assert found.violations == ()


def test_선택_필드를_전부_받는다():
    payload = {
        **MINIMAL,
        "project": "reply-gate",
        "occurred_at": "2026-08-05T03:23:31Z",
        "action": {"tool": "Bash", "paths": ["scripts/evaluate.py"], "argv_heads": ["cd", "uv"]},
    }
    found = parse_line(emit(payload))
    assert found.conforming
    assert found.fields["action"]["argv_heads"] == ["cd", "uv"]


def test_사유는_길어도_자르지_않는다():
    long_reason = "가" * 5000
    found = parse_line(emit({**MINIMAL, "reason": long_reason}))
    assert found.conforming
    assert found.fields["reason"] == long_reason


def test_사유_안의_개행은_이스케이프로_들어온다():
    found = parse_line(emit({**MINIMAL, "reason": "첫 줄\n둘째 줄"}))
    assert found.conforming
    assert found.fields["reason"] == "첫 줄\n둘째 줄"


@pytest.mark.parametrize("prefix", ["", "  ", "\t", " \t "])
@pytest.mark.parametrize("suffix", ["", "  ", "\t"])
def test_줄_앞뒤_공백을_허용한다(prefix, suffix):
    assert parse_line(f"{prefix}{emit(MINIMAL)}{suffix}").conforming


@pytest.mark.parametrize("guard", ["a", "0", "a-b", "a.b", "a1.b-c2", "x" * 64])
def test_식별자_문법_통과(guard):
    assert parse_line(emit({**MINIMAL, "guard": guard})).conforming


def test_한_줄에_한_사건():
    text = "\n".join([emit(MINIMAL), "그냥 로그", emit({**MINIMAL, "guard": "other"})])
    found = parse_text(text)
    assert [f.line_no for f in found] == [1, 3]
    assert [f.fields["guard"] for f in found] == ["evaluation-live-waste", "other"]


def test_표지_없는_줄은_감지되지_않는다():
    assert parse_line("거부: `evaluation-live-waste` 은 …") is None
    assert parse_text("아무 로그\n또 로그\n") == []


def test_사람용_출력과_같이_있어도_잡는다():
    """가드는 산문 메시지를 그대로 내고 기계용 한 줄을 추가로 낸다."""
    text = f"거부: `x` 은 라이브 실측 리포트 이름인데 …\n{emit(MINIMAL)}\n측정 1 — L1 픽스처 27건\n"
    found = parse_text(text)
    assert len(found) == 1
    assert found[0].line_no == 2


# ── 위반 ────────────────────────────────────────────────────────────────────


def test_필수_필드가_없으면_위반():
    found = parse_line(emit({"guard": "g"}))
    assert not found.conforming
    assert found.origin == RAW
    assert "missing_field" in found.violations
    assert found.fields is None


def test_사유가_공백뿐이면_위반():
    found = parse_line(emit({**MINIMAL, "reason": "   "}))
    assert "empty_reason" in found.violations


@pytest.mark.parametrize(
    "guard",
    ["Eval-Live", "eval live", "-eval", "eval-", ".eval", "eval_live", "", "x" * 65, "가드"],
)
def test_식별자_문법_위반(guard):
    found = parse_line(emit({**MINIMAL, "guard": guard}))
    assert "bad_guard_id" in found.violations


def test_미지정_키는_위반():
    """가드가 임의 키로 판정 쪽에 신호를 흘리는 채널을 막는다."""
    found = parse_line(emit({**MINIMAL, "verdict": "맞은 거부"}))
    assert "unknown_key" in found.violations


def test_미지원_버전은_감지되고_위반():
    found = parse_line(emit(MINIMAL, version=2))
    assert found is not None
    assert found.version == 2
    assert found.violations == ("unsupported_version",)
    assert found.fields is None


def test_JSON이_깨지면_위반():
    found = parse_line('REJECT-BENCH/1 {"guard":"g","reason":')
    assert "bad_json" in found.violations


@pytest.mark.parametrize("payload", ["[1,2]", '"문자열"', "42", "null", "true"])
def test_객체가_아니면_위반(payload):
    found = parse_line(emit(payload))
    assert "not_object" in found.violations


@pytest.mark.parametrize("value", [123, None, [], {}])
def test_사유_타입_위반(value):
    found = parse_line(emit({"guard": "g", "reason": value}))
    assert "bad_type" in found.violations


@pytest.mark.parametrize(
    "action",
    [
        "문자열",
        [],
        {},
        {"tool": "Bash", "extra": 1},
        {"tool": "uv run"},
        {"tool": ""},
        {"argv_heads": ["uv run python"]},
        {"argv_heads": [""]},
        {"argv_heads": "uv"},
        {"paths": [1]},
        {"paths": "scripts/"},
    ],
)
def test_행동_뼈대_위반(action):
    found = parse_line(emit({**MINIMAL, "action": action}))
    assert "bad_action" in found.violations


def test_인자_전문은_argv_heads에_못_들어간다():
    """'첫 토큰 목록'이라는 계약을 기계가 확인할 수 있는 유일한 지점."""
    ok = parse_line(emit({**MINIMAL, "action": {"argv_heads": ["uv", "cd"]}}))
    bad = parse_line(emit({**MINIMAL, "action": {"argv_heads": ["uv run --report-stem x"]}}))
    assert ok.conforming
    assert not bad.conforming


@pytest.mark.parametrize(
    "value",
    ["2026-08-05 03:23:31", "2026-08-05T03:23:31+00:00", "2026-08-05T03:23:31", "어제", 1234],
)
def test_발생_시각_형식_위반(value):
    found = parse_line(emit({**MINIMAL, "occurred_at": value}))
    assert "bad_type" in found.violations


def test_발생_시각_소수점은_허용():
    assert parse_line(emit({**MINIMAL, "occurred_at": "2026-08-05T03:23:31.5Z"})).conforming


@pytest.mark.parametrize("project", ["", "x" * 129, 1, None])
def test_프로젝트_필드_위반(project):
    found = parse_line(emit({**MINIMAL, "project": project}))
    assert "bad_type" in found.violations


def test_위반은_전부_모은다():
    found = parse_line(emit({"guard": "BAD ID", "extra": 1}, version=9))
    assert set(found.violations) >= {
        "unsupported_version",
        "missing_field",
        "unknown_key",
        "bad_guard_id",
    }


# ── 원시 보존과 식별자 복원 ─────────────────────────────────────────────────


def test_원시_줄_전문을_그대로_들고_있다():
    line = emit({"guard": "g"})
    found = parse_line(line)
    assert found.raw == line
    assert found.fields is None


def test_깨진_JSON에서도_식별자를_복원한다():
    found = parse_line('REJECT-BENCH/1 {"guard":"evaluation-live-waste","reason":')
    assert found.recovered_guard == "evaluation-live-waste"


def test_문법에_안_맞는_복원값은_버린다():
    """복원은 참고값이다 — 문법을 통과할 때만 성공으로 본다."""
    found = parse_line('REJECT-BENCH/1 {"guard":"Eval Live","reason":')
    assert found.recovered_guard is None


def test_복원할_식별자가_없으면_None():
    found = parse_line("REJECT-BENCH/1 {깨짐")
    assert found.recovered_guard is None


def test_준수한_줄에는_복원값이_없다():
    assert parse_line(emit(MINIMAL)).recovered_guard is None


# ── 경계 ────────────────────────────────────────────────────────────────────


def test_페이로드가_없으면_감지되고_위반():
    found = parse_line("REJECT-BENCH/1")
    assert found is not None
    assert found.violations == ("bad_json",)


def test_표지_뒤에_바로_문자가_붙으면_감지_안_함():
    assert parse_line("REJECT-BENCH/1garbage") is None
    assert parse_line("REJECT-BENCH/") is None
    assert parse_line("XREJECT-BENCH/1 {}") is None


def test_표지는_대소문자를_가린다():
    assert parse_line(emit(MINIMAL).replace("REJECT-BENCH", "reject-bench")) is None


def test_앞자리_0은_준수가_아니다():
    found = parse_line(emit(MINIMAL, version="01"))
    assert found is not None
    assert "unsupported_version" in found.violations


def test_큰_버전_번호도_감지된다():
    found = parse_line(emit(MINIMAL, version=999))
    assert found.version == 999
    assert "unsupported_version" in found.violations


def test_빈_텍스트():
    assert parse_text("") == []


def test_마지막_줄에_개행이_없어도_잡는다():
    assert len(parse_text(emit(MINIMAL))) == 1
