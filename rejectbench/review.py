"""사용자 유용성 검토 (spec §3.4, §3.6, §5 "세션 뒤" 4).

- 전수 검토 큐: amendment를 반영한 유효 출처가 `operation`인 등록 사건 중
  UtilityReview가 없는 전부를 append 순서로 나열한다. `review_queue`의
  입력은 dataset 하나뿐이다 — 선택적으로 불리한 사건을 빼는 매개변수를
  의도적으로 두지 않는다.
- 검토는 append 전용이다. 재검토도 이전 레코드를 보존하고 최신값은 파생
  조회(`Dataset.latest_review`)로만 드러난다. `uncertain`은 기록된
  보류값으로, 레코드가 없는 미처리와 구분된다.
- 검토 중 시험·강제 발동으로 확인된 사건은 사유 있는 amendment로 `test`
  강등한다 — T1 `amend_origin` 재사용, 원본 사건은 절대 덮지 않는다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from rejectbench.dataset import Dataset
from rejectbench.origin import OriginTransitionError, amend_origin
from rejectbench.records import Amendment, GuardEvent, Origin, Utility, UtilityReview
from rejectbench.store import AppendStore


class ReviewError(Exception):
    """검토 경로 위반 — 존재하지 않는 사건, 사유 없는 강등 등."""


@dataclass(frozen=True)
class ReviewQueue:
    """전수 검토 큐. pending은 append 순서 그대로다."""

    pending_event_ids: tuple[str, ...]
    reviewed: int
    test: int
    unknown: int
    unregistered: int


def review_queue(dataset: Dataset) -> ReviewQueue:
    """UtilityReview가 없는 새 `operation` 사건 전부 (유효 출처 기준)."""
    pending: list[str] = []
    reviewed = test = unknown = unregistered = 0
    for event_id, event in dataset.events.items():
        if event.unregistered:
            unregistered += 1
            continue
        effective = dataset.effective_origin(event_id)
        if effective is Origin.TEST:
            test += 1
            continue
        if effective is Origin.UNKNOWN:
            unknown += 1
            continue
        if dataset.latest_review(event_id) is None:
            pending.append(event_id)
        else:
            reviewed += 1
    return ReviewQueue(
        pending_event_ids=tuple(pending),
        reviewed=reviewed,
        test=test,
        unknown=unknown,
        unregistered=unregistered,
    )


def _load_dataset(store: AppendStore) -> Dataset:
    return Dataset(store.load().records)


def _require_event(dataset: Dataset, event_id: str) -> GuardEvent:
    event = dataset.events.get(event_id)
    if event is None:
        raise ReviewError(f"사건이 없다: {event_id}")
    return event


def record_review(
    store: AppendStore,
    *,
    event_id: str,
    utility: Utility,
    note: str = "",
    reviewed_at: datetime | None = None,
    review_id: str | None = None,
) -> UtilityReview:
    """검토 레코드를 append한다. 재검토도 이전 레코드를 보존한다."""
    if not isinstance(utility, Utility):
        raise ReviewError(f"utility: Utility 값이어야 한다 ({utility!r})")
    dataset = _load_dataset(store)
    _require_event(dataset, event_id)
    review = UtilityReview(
        review_id=review_id or f"rv-{uuid.uuid4().hex}",
        event_id=event_id,
        utility=utility,
        note=note,
        reviewed_at=reviewed_at or datetime.now(timezone.utc),
    )
    store.append(review)
    return review


def demote_to_test(
    store: AppendStore,
    *,
    event_id: str,
    reason: str,
    amended_at: datetime | None = None,
    amendment_id: str | None = None,
) -> Amendment:
    """시험·강제 발동으로 확인된 사건의 `test` 강등 amendment를 append한다.

    사유는 필수다. 유효 출처 기준으로 전이를 검사하므로 이미 강등된 사건의
    중복 강등과 금지 전이는 거부된다 (`operation`/`unknown`→`test`만 허용).
    """
    if not isinstance(reason, str) or not reason.strip():
        raise ReviewError("reason: test 강등에는 사유가 필수다")
    dataset = _load_dataset(store)
    event = _require_event(dataset, event_id)
    current = dataset.effective_origin(event_id)
    try:
        amendment = amend_origin(
            event,
            new_origin=Origin.TEST,
            reason=reason,
            amendment_id=amendment_id or f"am-{uuid.uuid4().hex}",
            amended_at=amended_at or datetime.now(timezone.utc),
            current_origin=current,
        )
    except OriginTransitionError as exc:
        raise ReviewError(str(exc)) from exc
    store.append(amendment)
    return amendment
