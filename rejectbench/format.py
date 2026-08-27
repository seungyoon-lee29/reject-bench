"""거부 출력 형식 v1 파서.

정본은 `docs/형식-표준.md`다. 파서가 하는 일은 둘뿐이다 — 감지된 줄을
구조화하거나, 못 하면 **원시 줄 전문을 그대로 들고** 위반 사유를 붙인다.
판정도 마스킹도 여기서 하지 않는다(마스킹은 적재 시점 — spec 3.1).

    python -m rejectbench.format < 출력.txt
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field

from rejectbench.rules import (
    ACTION_KEYS,
    ALLOWED_KEYS,
    DETECT_RE,
    GUARD_ID_MAX,
    GUARD_ID_RE,
    GUARD_SALVAGE_RE,
    PROJECT_MAX,
    REQUIRED_KEYS,
    RFC3339_UTC_RE,
    SUPPORTED_VERSIONS,
)

#: spec 3.1의 출처 표시값.
CONFORMING = "형식 준수"
RAW = "비정형(원시 보존)"

#: "파싱 안 됨"과 "None으로 파싱됨"을 가르는 표식.
_UNPARSED = object()


@dataclass(frozen=True)
class Detected:
    """감지된 줄 하나 = 사건 하나."""

    line_no: int
    """스캔한 텍스트 안에서의 줄 번호(1-based)."""

    raw: str
    """원시 줄 전문. 파서는 이것을 고쳐 쓰지 않는다."""

    version: int
    fields: dict | None = None
    """형식 준수일 때만 채워진다."""

    violations: tuple[str, ...] = ()
    recovered_guard: str | None = None
    """JSON이 깨졌을 때의 식별자 복원 결과. 참고값이다."""

    @property
    def conforming(self) -> bool:
        return not self.violations

    @property
    def origin(self) -> str:
        """spec 3.1 출처 표시."""
        return CONFORMING if self.conforming else RAW


@dataclass
class _Check:
    violations: list[str] = field(default_factory=list)

    def add(self, code: str) -> None:
        if code not in self.violations:
            self.violations.append(code)


def _check_guard(value, check: _Check) -> None:
    if not isinstance(value, str):
        check.add("bad_type")
        return
    if len(value) > GUARD_ID_MAX or not GUARD_ID_RE.match(value):
        check.add("bad_guard_id")


def _check_reason(value, check: _Check) -> None:
    if not isinstance(value, str):
        check.add("bad_type")
        return
    if not value.strip():
        check.add("empty_reason")


def _check_project(value, check: _Check) -> None:
    if not isinstance(value, str):
        check.add("bad_type")
        return
    if not 1 <= len(value) <= PROJECT_MAX:
        check.add("bad_type")


def _check_occurred_at(value, check: _Check) -> None:
    if not isinstance(value, str):
        check.add("bad_type")
        return
    if not RFC3339_UTC_RE.match(value):
        check.add("bad_type")


def _check_action(value, check: _Check) -> None:
    """행동 뼈대. `argv_heads`의 공백 금지가 '인자 전문 금지'를 기계가 볼 수
    있는 유일한 지점이다(형식 문서 §1.3)."""
    if not isinstance(value, dict):
        check.add("bad_action")
        return
    if set(value) - ACTION_KEYS or not value:
        check.add("bad_action")
        return

    tool = value.get("tool")
    if tool is not None and (not isinstance(tool, str) or not tool or _has_space(tool)):
        check.add("bad_action")

    for key in ("paths", "argv_heads"):
        items = value.get(key)
        if items is None:
            continue
        if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
            check.add("bad_action")
            continue
        if key == "argv_heads" and any(_has_space(i) or not i for i in items):
            check.add("bad_action")


def _has_space(text: str) -> bool:
    return any(ch.isspace() for ch in text)


_FIELD_CHECKS = {
    "guard": _check_guard,
    "reason": _check_reason,
    "project": _check_project,
    "action": _check_action,
    "occurred_at": _check_occurred_at,
}


def parse_line(line: str, line_no: int = 1) -> Detected | None:
    """감지되면 `Detected`, 아니면 None."""
    match = DETECT_RE.match(line)
    if match is None:
        return None

    raw = line.rstrip("\n")
    digits = match.group(1)
    version = int(digits)
    payload = match.group(2)
    check = _Check()

    # `/01`은 감지는 하되 준수로 보지 않는다 — 버전 표기를 흔들면 하위호환
    # 판단이 표기에 의존하게 된다.
    if version not in SUPPORTED_VERSIONS or digits != str(version):
        check.add("unsupported_version")

    # `null`은 JSON으로 유효하지만 객체가 아니다 — "파싱 안 됨"과 "None으로
    # 파싱됨"을 구분하지 않으면 `REJECT-BENCH/1 null`이 준수로 통과한다.
    parsed = _UNPARSED
    if payload:
        try:
            parsed = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            check.add("bad_json")
    else:
        check.add("bad_json")

    if parsed is not _UNPARSED and not isinstance(parsed, dict):
        check.add("not_object")
        parsed = _UNPARSED

    if isinstance(parsed, dict):
        for key in REQUIRED_KEYS:
            if key not in parsed:
                check.add("missing_field")
        if set(parsed) - ALLOWED_KEYS:
            check.add("unknown_key")
        for key, value in parsed.items():
            checker = _FIELD_CHECKS.get(key)
            if checker is not None:
                checker(value, check)

    # 위반이 하나라도 있으면 구조화 결과를 내지 않는다 — 반쪽 구조화본은
    # 생산 태스크가 준수본으로 착각할 수 있다.
    conforming = not check.violations
    as_dict = parsed if isinstance(parsed, dict) else None
    return Detected(
        line_no=line_no,
        raw=raw,
        version=version,
        fields=as_dict if conforming else None,
        violations=tuple(check.violations),
        recovered_guard=None if conforming else _salvage_guard(payload, as_dict),
    )


def _salvage_guard(payload: str | None, parsed: dict | None) -> str | None:
    """식별자 복원 시도. 문법을 통과할 때만 복원 성공으로 본다."""
    candidate = None
    if isinstance(parsed, dict) and isinstance(parsed.get("guard"), str):
        candidate = parsed["guard"]
    elif payload:
        found = GUARD_SALVAGE_RE.search(payload)
        if found:
            candidate = found.group(1)

    if candidate is None:
        return None
    if len(candidate) > GUARD_ID_MAX or not GUARD_ID_RE.match(candidate):
        return None
    return candidate


def parse_text(text: str) -> list[Detected]:
    """텍스트 덩어리에서 감지된 줄을 전부. 1줄 = 1사건."""
    return [
        found
        for line_no, line in enumerate(text.splitlines(), start=1)
        if (found := parse_line(line, line_no)) is not None
    ]


def main(argv: list[str] | None = None) -> int:
    """표준 입력을 훑어 감지 결과를 JSONL로 낸다 — 배선 점검용."""
    del argv
    for found in parse_text(sys.stdin.read()):
        print(
            json.dumps(
                {
                    "line_no": found.line_no,
                    "origin": found.origin,
                    "version": found.version,
                    "violations": list(found.violations),
                    "guard": (found.fields or {}).get("guard") or found.recovered_guard,
                },
                ensure_ascii=False,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
