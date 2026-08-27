"""감지 규칙 + 후보 규칙 + 규칙 버전.

정본은 `docs/형식-표준.md`다. 이 모듈은 그 문서의 규칙을 기계가 쓸 수 있는
형태로 옮긴 것이고, 둘이 어긋나면 문서가 이긴다.

**규칙 버전 = 이 파일과 형식 문서를 마지막으로 건드린 커밋 해시.** 거부
레코드와 판정 레코드가 이 해시를 참조하므로(spec 3.1), 규칙을 고치면 커밋을
거쳐야 버전이 움직인다.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

from rejectbench.paths import REPO_ROOT

# ── 감지 규칙 v1 ────────────────────────────────────────────────────────────

#: 표지 뒤는 공백·탭이거나 줄끝이어야 한다. 페이로드가 깨졌어도 감지는 된다 —
#: 형식을 쓰려다 실패한 거부가 감지 단계에서 사라지면 안 된다.
DETECT_RE = re.compile(r"^[ \t]*REJECT-BENCH/([0-9]+)(?:[ \t]+(.*?))?[ \t]*$")

#: v1 파서가 구조화하는 버전. 나머지는 감지되고 비정형으로 분류된다.
SUPPORTED_VERSIONS = frozenset({1})

#: 가드 식별자 문법 — 등록부 연결 키(spec 3.2).
GUARD_ID_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$")

GUARD_ID_MAX = 64
PROJECT_MAX = 128

#: JSON이 깨졌을 때의 식별자 복원 시도. 복원값은 참고일 뿐이고 판정 가능
#: 여부는 등록부 등재가 정한다.
GUARD_SALVAGE_RE = re.compile(r'"guard"\s*:\s*"([^"]*)"')

RFC3339_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")

REQUIRED_KEYS = ("guard", "reason")
OPTIONAL_KEYS = ("project", "action", "occurred_at")
ALLOWED_KEYS = frozenset(REQUIRED_KEYS + OPTIONAL_KEYS)
ACTION_KEYS = frozenset({"tool", "paths", "argv_heads"})

#: 위반 사유 코드 — 집계에 쓰이므로 문자열을 고정한다(형식 문서 §4).
VIOLATION_CODES = (
    "unsupported_version",
    "bad_json",
    "not_object",
    "missing_field",
    "bad_type",
    "bad_guard_id",
    "unknown_key",
    "empty_reason",
    "bad_action",
)


# ── 후보 규칙 v1 ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Marker:
    """v0 자유문에서 후보를 고르는 마커.

    `source`가 이 표의 핵심이다. 가드 코드에서 뽑은 마커와 로그를 훑다 발견한
    마커를 구분하지 않으면, 데이터에 맞춰 만든 규칙으로 잡은 건수가 나중에
    선등록 규칙의 성과로 읽힌다.
    """

    code: str
    text: str
    source: str
    post_hoc: bool
    note: str


CANDIDATE_MARKERS = (
    Marker("M1", "거부:", "가드 코드 (reply-gate 6e585f9)", False, "398행 계열"),
    Marker("M2", "는 지금 돌릴 수 없다", "가드 코드 (reply-gate 7efc966)", False, "628행 계열"),
    Marker("M3", "은 지금 돌릴 수 없다", "가드 코드 (같은 계열 조사 변형)", False, "--stub-llm 자매 가드"),
    Marker("M4", "가드: 거부", "로그 관찰", True, "2026-08-12 세션에서 사람이 v0 로그를 훑다 발견 — 선등록 아님"),
    Marker("M5", "REJECT-BENCH/", "형식 정의", False, "정형 출력이 어댑터를 못 거치고 v0로 샌 경우"),
)

#: 스캐너 배제 4종(spec 3.3). (d)는 불확실하면 배제하지 말고 큐로 보낸다.
EXCLUSION_CODES = {
    "a": "자기 참조 오염",
    "b": "기기록 v0 행",
    "c": "기탈락 후보",
    "d": "기록 사건 매칭 행",
}

QUEUE_DECISIONS = ("이관", "탈락", "중복")


def candidate_markers(text: str) -> tuple[str, ...]:
    """자유문에서 걸린 마커 코드들. 하나라도 걸리면 후보다."""
    if not text:
        return ()
    return tuple(m.code for m in CANDIDATE_MARKERS if m.text in text)


def is_candidate(text: str) -> bool:
    return bool(candidate_markers(text))


# ── 규칙 버전 ───────────────────────────────────────────────────────────────

#: 이 경로들의 마지막 커밋이 규칙 버전이다.
RULE_PATHS = ("docs/형식-표준.md", "rejectbench/rules.py")


def rules_version() -> str | None:
    """규칙 버전(커밋 해시). 이력이 없으면 None.

    읽기 전용 git 조회다 — 불변식 7의 "임의 코드 실행 금지"는 기록·판정
    대상에서 유래한 코드를 두고 하는 말이고 여기 해당하지 않는다.

    None이 나오는 경우: 아직 커밋 안 됨, git 없음, 저장소 밖. 호출자는 이
    값을 레코드에 그대로 실어 "규칙 버전 미상"이 보이게 한다 — 임의로 채우면
    미상이 사라진다.
    """
    try:
        done = subprocess.run(
            ["git", "log", "-1", "--format=%H", "--", *RULE_PATHS],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.strip() or None
