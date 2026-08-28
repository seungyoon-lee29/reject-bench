"""출처 결정표와 출처 정정 규칙 (spec §3.2).

T1 범위에서 이 모듈은 순수 결정 규칙이다. 세션 test 플래그의 런타임 배선은
T3 소관이며, 여기서는 플래그 상태를 입력으로만 받는다.

결정표:

1. 세션 test 플래그가 켜져 있으면 항상 `test` (`explicit_flag`).
2. 플래그가 없으면 `operation` + `origin_evidence: default_inherited`.
3. 생성 시점에 실행 맥락 정보를 얻지 못했으면 `unknown` (`no_context`).

정정은 append amendment로만 한다. 허용 전이는 `operation→test`,
`unknown→test`(사유 필수) 뿐이고 어떤 값에서도 `operation`으로의 승격은
금지다 — 특히 `unknown→operation` 승격 금지.
"""

from __future__ import annotations

from datetime import datetime

from rejectbench.hashing import value_hash
from rejectbench.records import Amendment, GuardEvent, Origin, OriginEvidence

ORIGIN_FIELD = "origin"


class OriginTransitionError(ValueError):
    """허용되지 않는 출처 전이."""


def decide_origin(
    *, context_available: bool, test_flag: bool = False
) -> tuple[Origin, OriginEvidence]:
    """생성 시점의 출처 결정표. 순수 함수 — 상태를 읽지 않는다."""
    if not context_available:
        # 맥락 자체가 없으면 플래그 값은 근거가 될 수 없다.
        return (Origin.UNKNOWN, OriginEvidence.NO_CONTEXT)
    if test_flag:
        return (Origin.TEST, OriginEvidence.EXPLICIT_FLAG)
    return (Origin.OPERATION, OriginEvidence.DEFAULT_INHERITED)


def validate_origin_transition(old: Origin, new: Origin) -> None:
    """amendment로 허용되는 출처 전이인지 검사한다."""
    if new is Origin.TEST and old in (Origin.OPERATION, Origin.UNKNOWN):
        return
    raise OriginTransitionError(
        f"출처 전이 금지: {old.value} → {new.value} "
        "(허용: operation→test, unknown→test)"
    )


def amend_origin(
    event: GuardEvent,
    *,
    new_origin: Origin,
    reason: str,
    amendment_id: str,
    amended_at: datetime,
    current_origin: Origin | None = None,
) -> Amendment:
    """사유 있는 출처 정정 amendment를 만든다. 원본 사건은 건드리지 않는다.

    `current_origin`은 이미 적용된 amendment가 있을 때의 유효 출처다.
    주지 않으면 기록된 출처를 기준으로 한다.
    """
    old = current_origin if current_origin is not None else event.origin
    validate_origin_transition(old, new_origin)
    return Amendment(
        amendment_id=amendment_id,
        target_id=event.event_id,
        field=ORIGIN_FIELD,
        previous_value_hash=value_hash(old.value),
        new_value=new_origin.value,
        reason=reason,
        amended_at=amended_at,
    )
