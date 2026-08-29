"""로컬 Markdown 보고서 (spec §6, §4, §9).

- 모든 비율은 원수 `N/D`와 백분율을 병기한다. 분모 0은 `미검증`이며 성공이
  아니다. 분자는 분모의 부분집합으로만 센다 (`Ratio` 구성 강제).
- 판정 가능 가드·확정값 정의는 `metrics`의 단일 정의를 그대로 재사용한다
  (`decision_completion`, `operation_event_ids`, `verdict_status`,
  `review_status`). 이 모듈은 지표를 재정의하지 않는다.
- `post-remove` 발동 수는 T5 파생 판별(`post_remove_event_ids`)로 센다 —
  레코드에 저장된 값만 믿지 않는다.
- 시험 사건은 운영 지표의 분자·분모에서 제외하고 "기술 검증" 절에만
  `기술 검증용 test evidence`로 별도 표시한다.
- 비노출 계약 (spec §4): 보고서에 홈 경로·세션 식별자를 넣지 않는다.
  세션은 수만 세고, 사건 id·enforcement 경로·guard_hint도 렌더링하지 않는다.
  경로 표기는 store 루트 상대 파일명(records.jsonl 등)뿐이며, 마지막 방어선
  으로 렌더링 결과의 홈 경로 문자열을 `~`로 치환한다.
- 관측 도중 추가된 신규 가드는 append 순서로 판별한다: 첫 GuardEvent보다
  뒤에 첫 GuardSpec이 append된 가드다.
- 기준선 측정 결과는 store 루트의 사이드카 `baseline.json`(단일 JSON 객체)
  에서 읽고, 없으면 "미측정"을 명시한다. 판정기 교정 상태는 사이드카
  `calibration.jsonl`에서 `load_calibrations`로 읽는다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rejectbench.dataset import Dataset
from rejectbench.decision import build_guard_view, post_remove_event_ids
from rejectbench.judge import CALIBRATION_FILENAME, load_calibrations
from rejectbench.metrics import (
    Completion,
    Status,
    decision_completion,
    operation_event_ids,
    review_status,
    verdict_status,
)
from rejectbench.origin import ORIGIN_FIELD
from rejectbench.records import (
    SCHEMA_VERSION,
    Amendment,
    CaptureStatus,
    GuardEvent,
    GuardSpec,
    LossKind,
    LossRecord,
    Origin,
    Utility,
    Verdict,
)
from rejectbench.rubric import rubric_hash
from rejectbench.scrub import scrub_text
from rejectbench.store import RECORDS_FILENAME, AppendStore

REPORTS_DIRNAME = "reports"
BASELINE_FILENAME = "baseline.json"
OBSERVATION_PROTOCOL_REF = "docs/관찰-프로토콜.md"

TEST_EVIDENCE_MARK = "기술 검증용 test evidence"
TEST_EVIDENCE_ONLY_MARK = "test evidence only"
OPERATION_UNVERIFIED_MARK = "운영: 미검증"
UNVERIFIED_TEXT = "미검증 (분모 0 — 성공 아님)"


# --- 원수 병기 ----------------------------------------------------------------


@dataclass(frozen=True)
class Ratio:
    """원수 `N/D`. 분자⊆분모는 구성상 강제되고, 분모 0은 미검증이다."""

    numerator: int
    denominator: int

    def __post_init__(self):
        assert 0 <= self.numerator <= self.denominator or (
            self.denominator == 0 and self.numerator == 0
        )

    @property
    def unverified(self) -> bool:
        return self.denominator == 0

    @property
    def fraction(self) -> str:
        return f"{self.numerator}/{self.denominator}"

    @property
    def percentage(self) -> float | None:
        if self.unverified:
            return None
        return 100.0 * self.numerator / self.denominator

    def render(self) -> str:
        if self.unverified:
            return UNVERIFIED_TEXT
        return f"{self.fraction} ({self.percentage:.1f}%)"


@dataclass(frozen=True)
class PendingStats:
    """한 판단 축의 미처리(레코드 없음)와 보류(기록된 보류값) 현황."""

    unprocessed: int
    unprocessed_max_elapsed: timedelta | None
    held: int
    held_max_elapsed: timedelta | None


@dataclass(frozen=True)
class GuardReportRow:
    guard_id: str
    project: str
    latest_version: int
    enforcement_status: str
    operation_session_count: int
    decidable_session_count: int
    guard_decidable: bool
    operation_events: int
    test_events: int
    unknown_events: int
    decision_count: int
    countable_decision_count: int
    no_event_decision_count: int
    post_remove_count: int
    drift_count: int
    new_during_observation: bool


@dataclass(frozen=True)
class CalibrationRow:
    guard_label: str
    model_id: str
    passed: bool
    examples_passed: int
    examples_total: int
    current_rubric: bool


@dataclass(frozen=True)
class ReportData:
    generated_at: datetime
    record_count: int
    corrupt_line_count: int
    completion: Completion
    policy_mismatch: Ratio
    unnecessary_block: Ratio
    disagreement: Ratio
    correct_but_unnecessary: Ratio
    incorrect_but_useful: Ratio
    operation_count: int
    test_count: int
    unknown_count: int
    unregistered_count: int
    verdict_pending: PendingStats
    review_pending: PendingStats
    loss_total: int
    loss_by_kind: tuple[tuple[str, int], ...]
    partial_capture_count: int
    amendment_count: int
    demotion_count: int
    drift_count: int
    post_remove_count: int
    guards: tuple[GuardReportRow, ...]
    new_guard_ids: tuple[str, ...]
    test_events_by_guard: tuple[tuple[str, int], ...]
    baseline_lines: tuple[str, ...]
    baseline_note: str | None
    calibration_rows: tuple[CalibrationRow, ...]
    calibration_record_count: int
    calibration_corrupt_count: int


# --- 수집 ---------------------------------------------------------------------


def new_guard_ids(dataset: Dataset) -> frozenset[str]:
    """첫 GuardEvent보다 뒤에 첫 spec이 append된 가드 — 관측 도중 추가."""
    first_event_pos: int | None = None
    first_spec_pos: dict[str, int] = {}
    for pos, record in enumerate(dataset.records):
        if isinstance(record, GuardEvent):
            if first_event_pos is None:
                first_event_pos = pos
        elif isinstance(record, GuardSpec):
            first_spec_pos.setdefault(record.guard_id, pos)
    if first_event_pos is None:
        return frozenset()
    return frozenset(
        guard_id for guard_id, pos in first_spec_pos.items() if pos > first_event_pos
    )


def _max_elapsed(now: datetime, moments: list[datetime]) -> timedelta | None:
    if not moments:
        return None
    return max(max(now - moment, timedelta(0)) for moment in moments)


def _pending_stats(
    dataset: Dataset,
    op_ids: list[str],
    *,
    now: datetime,
    status_of,
    held_time_of,
) -> PendingStats:
    unprocessed_times: list[datetime] = []
    held_times: list[datetime] = []
    for event_id in op_ids:
        status = status_of(dataset, event_id)
        if status is Status.UNPROCESSED:
            unprocessed_times.append(dataset.events[event_id].occurred_at)
        elif status is Status.HELD:
            held_times.append(held_time_of(dataset, event_id))
    return PendingStats(
        unprocessed=len(unprocessed_times),
        unprocessed_max_elapsed=_max_elapsed(now, unprocessed_times),
        held=len(held_times),
        held_max_elapsed=_max_elapsed(now, held_times),
    )


def _load_baseline(root: Path) -> tuple[tuple[str, ...], str | None]:
    path = root / BASELINE_FILENAME
    if not path.exists():
        return (), None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return (), f"{BASELINE_FILENAME}을 읽을 수 없다 — 미측정으로 간주한다"
    if not isinstance(payload, dict) or not payload:
        return (), f"{BASELINE_FILENAME}이 JSON 객체가 아니다 — 미측정으로 간주한다"
    lines = []
    for key, value in payload.items():
        rendered = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        lines.append(scrub_text(f"{key}: {rendered}"))
    return tuple(lines), None


def _calibration_rows(dataset: Dataset, root: Path) -> tuple[tuple[CalibrationRow, ...], int, int]:
    records, corrupt = load_calibrations(root)
    labels: dict[str, str] = {}
    for (guard_id, version), spec in sorted(dataset.specs_by_key.items()):
        labels.setdefault(spec.content_hash, f"{guard_id} v{version}")
    current = rubric_hash()
    latest: dict[tuple[str, str, str, str], object] = {}
    for record in records:
        key = (
            record.guard_spec_hash,
            record.model_id,
            record.model_settings_hash,
            record.rubric_hash,
        )
        latest[key] = record
    rows = tuple(
        CalibrationRow(
            guard_label=labels.get(record.guard_spec_hash, "(등록부에 없는 spec)"),
            model_id=record.model_id,
            passed=record.passed,
            examples_passed=record.examples_passed,
            examples_total=record.examples_total,
            current_rubric=record.rubric_hash == current,
        )
        for record in latest.values()
    )
    return rows, len(records), corrupt


def build_report(store: AppendStore, *, now: datetime | None = None) -> ReportData:
    now = now if now is not None else datetime.now(timezone.utc)
    load = store.load()
    dataset = Dataset(load.records)

    op_ids = operation_event_ids(dataset)
    verdict_confirmed = [
        event_id
        for event_id in op_ids
        if verdict_status(dataset, event_id) is Status.CONFIRMED
    ]
    review_confirmed = [
        event_id
        for event_id in op_ids
        if review_status(dataset, event_id) is Status.CONFIRMED
    ]
    both_confirmed = [
        event_id for event_id in verdict_confirmed if event_id in set(review_confirmed)
    ]

    def _verdict(event_id: str) -> Verdict:
        return dataset.latest_verdict(event_id).verdict

    def _utility(event_id: str) -> Utility:
        return dataset.latest_review(event_id).utility

    incorrect = sum(
        1 for event_id in verdict_confirmed if _verdict(event_id) is Verdict.INCORRECT_BLOCK
    )
    unnecessary = sum(
        1 for event_id in review_confirmed if _utility(event_id) is Utility.UNNECESSARY
    )
    correct_unnecessary = sum(
        1
        for event_id in both_confirmed
        if _verdict(event_id) is Verdict.CORRECT_BLOCK
        and _utility(event_id) is Utility.UNNECESSARY
    )
    incorrect_useful = sum(
        1
        for event_id in both_confirmed
        if _verdict(event_id) is Verdict.INCORRECT_BLOCK
        and _utility(event_id) is Utility.USEFUL
    )

    # 사건 출처 집계 — unregistered는 출처와 무관하게 별도, 나머지는 유효 출처.
    test_count = unknown_count = unregistered_count = 0
    for event_id, event in dataset.events.items():
        if event.unregistered:
            unregistered_count += 1
            continue
        effective = dataset.effective_origin(event_id)
        if effective is Origin.TEST:
            test_count += 1
        elif effective is Origin.UNKNOWN:
            unknown_count += 1

    verdict_pending = _pending_stats(
        dataset,
        op_ids,
        now=now,
        status_of=verdict_status,
        held_time_of=lambda ds, event_id: ds.latest_verdict(event_id).judged_at,
    )
    review_pending = _pending_stats(
        dataset,
        op_ids,
        now=now,
        status_of=review_status,
        held_time_of=lambda ds, event_id: ds.latest_review(event_id).reviewed_at,
    )

    losses = [record for record in load.records if isinstance(record, LossRecord)]
    loss_by_kind = tuple(
        (kind.value, sum(1 for loss in losses if loss.kind is kind)) for kind in LossKind
    )
    amendments = [record for record in load.records if isinstance(record, Amendment)]
    demotions = sum(
        1
        for amendment in amendments
        if amendment.field == ORIGIN_FIELD and amendment.new_value == Origin.TEST.value
    )
    partial = sum(
        1
        for event in dataset.events.values()
        if event.capture_status is CaptureStatus.PARTIAL
    )
    drift = sum(1 for event in dataset.events.values() if event.drift)

    new_guards = new_guard_ids(dataset)
    guard_rows: list[GuardReportRow] = []
    test_by_guard: list[tuple[str, int]] = []
    for guard_id in sorted({gid for gid, _ in dataset.specs_by_key}):
        view = build_guard_view(dataset, guard_id)
        op_events = sum(
            1 for row in view.events if row.effective_origin is Origin.OPERATION
        )
        test_events = sum(1 for row in view.events if row.effective_origin is Origin.TEST)
        unknown_events = sum(
            1 for row in view.events if row.effective_origin is Origin.UNKNOWN
        )
        countable = sum(1 for d in view.decisions if d.annotation.countable)
        no_event = sum(1 for d in view.decisions if d.annotation.no_event_guard)
        guard_rows.append(
            GuardReportRow(
                guard_id=guard_id,
                project=view.project,
                latest_version=view.latest_version,
                enforcement_status=view.enforcement.status.value,
                operation_session_count=view.operation_session_count,
                decidable_session_count=view.decidable_session_count,
                guard_decidable=view.guard_decidable,
                operation_events=op_events,
                test_events=test_events,
                unknown_events=unknown_events,
                decision_count=len(view.decisions),
                countable_decision_count=countable,
                no_event_decision_count=no_event,
                post_remove_count=view.post_remove_count,
                drift_count=sum(1 for row in view.events if row.drift),
                new_during_observation=guard_id in new_guards,
            )
        )
        if test_events:
            test_by_guard.append((guard_id, test_events))

    baseline_lines, baseline_note = _load_baseline(store.root)
    calibration_rows, calibration_count, calibration_corrupt = _calibration_rows(
        dataset, store.root
    )

    return ReportData(
        generated_at=now,
        record_count=len(load.records),
        corrupt_line_count=len(load.corrupt),
        completion=decision_completion(dataset),
        policy_mismatch=Ratio(incorrect, len(verdict_confirmed)),
        unnecessary_block=Ratio(unnecessary, len(review_confirmed)),
        disagreement=Ratio(correct_unnecessary + incorrect_useful, len(both_confirmed)),
        correct_but_unnecessary=Ratio(correct_unnecessary, len(both_confirmed)),
        incorrect_but_useful=Ratio(incorrect_useful, len(both_confirmed)),
        operation_count=len(op_ids),
        test_count=test_count,
        unknown_count=unknown_count,
        unregistered_count=unregistered_count,
        verdict_pending=verdict_pending,
        review_pending=review_pending,
        loss_total=len(losses),
        loss_by_kind=loss_by_kind,
        partial_capture_count=partial,
        amendment_count=len(amendments),
        demotion_count=demotions,
        drift_count=drift,
        post_remove_count=len(post_remove_event_ids(dataset)),
        guards=tuple(guard_rows),
        new_guard_ids=tuple(sorted(new_guards)),
        test_events_by_guard=tuple(test_by_guard),
        baseline_lines=baseline_lines,
        baseline_note=baseline_note,
        calibration_rows=calibration_rows,
        calibration_record_count=calibration_count,
        calibration_corrupt_count=calibration_corrupt,
    )


# --- 렌더링 -------------------------------------------------------------------


def _format_elapsed(delta: timedelta) -> str:
    seconds = max(int(delta.total_seconds()), 0)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}일 {hours}시간"
    if hours:
        return f"{hours}시간 {minutes}분"
    return f"{minutes}분"


def _pending_text(count: int, elapsed: timedelta | None) -> str:
    if count == 0 or elapsed is None:
        return f"{count}건"
    return f"{count}건 (최장 경과 {_format_elapsed(elapsed)})"


def render_report(data: ReportData) -> str:
    completion_ratio = Ratio(data.completion.numerator, data.completion.denominator)
    lines: list[str] = []
    add = lines.append

    add("# Reject Bench 보고서")
    add("")
    add(f"- 생성 시각: {data.generated_at.isoformat()} (UTC)")
    add(f"- 스키마 버전: {SCHEMA_VERSION}")
    add(
        f"- 레코드: {data.record_count}건 ({RECORDS_FILENAME} — "
        "경로는 store 루트 상대 표기만 쓴다)"
    )
    add("")

    add("## 상태")
    add("")
    if completion_ratio.unverified:
        add(f"- {OPERATION_UNVERIFIED_MARK} — 판정 가능 가드 0 (분모 0은 성공이 아니다)")
    else:
        add(f"- 운영: 증거 기반 결정 완료율 {completion_ratio.render()}")
    add(
        f"- 기술 검증: 시험 사건 {data.test_count}건 — \"기술 검증\" 절 참조 "
        "(운영 지표 제외)"
    )
    add("")

    add("## 대표 지표")
    add("")
    add("증거 기반 결정 완료율 = 결정과 근거가 기록된 판정 가능 가드 / 판정 가능 가드")
    add("")
    add(f"- 값: {completion_ratio.render()}")
    add(
        "- 판정 가능 가드: 서로 다른 둘 이상의 operation 세션 사건이 있고, 그 사건들의 "
        "정책 판정·유용성 검토가 모두 확정값인 가드. 분자는 이 분모의 부분집합으로만 센다."
    )
    add("")

    add("## 진단 지표")
    add("")
    add(
        "- 정책 불일치율 (incorrect_block / 정책 판정 확정 operation 사건): "
        f"{data.policy_mismatch.render()}"
    )
    add(
        "- 사용자 불필요 차단율 (unnecessary / 유용성 검토 확정 operation 사건): "
        f"{data.unnecessary_block.render()}"
    )
    add(
        "- LLM-사용자 불일치율 (방향 불일치 / 두 판단 확정 operation 사건): "
        f"{data.disagreement.render()}"
    )
    add(
        "  - 방향 — 정책상 옳은 차단 + 사용자 불필요: "
        f"{data.correct_but_unnecessary.render()}"
    )
    add(
        "  - 방향 — 정책상 그른 차단 + 사용자 유용: "
        f"{data.incorrect_but_useful.render()}"
    )
    add(
        "- 보류값(insufficient_context·uncertain)과 미처리 사건은 세 지표의 분모에서 "
        "모두 제외했다."
    )
    add("")

    add("## 사건 출처")
    add("")
    add(
        f"- 사건 수(유효 출처 기준, amendment 반영): operation {data.operation_count} · "
        f"test {data.test_count} · unknown {data.unknown_count} · "
        f"unregistered {data.unregistered_count}"
    )
    add(
        "- test·unknown·unregistered 사건은 운영 지표의 분자·분모에 들어가지 않는다. "
        "미등록(unregistered) 발동은 출처와 무관하게 건수만 병기한다."
    )
    add("")

    add("## 미처리와 보류")
    add("")
    add(
        "- PolicyVerdict — 미처리 "
        f"{_pending_text(data.verdict_pending.unprocessed, data.verdict_pending.unprocessed_max_elapsed)}"
        " · 보류(insufficient_context) "
        f"{_pending_text(data.verdict_pending.held, data.verdict_pending.held_max_elapsed)}"
    )
    add(
        "- UtilityReview — 미처리 "
        f"{_pending_text(data.review_pending.unprocessed, data.review_pending.unprocessed_max_elapsed)}"
        " · 보류(uncertain) "
        f"{_pending_text(data.review_pending.held, data.review_pending.held_max_elapsed)}"
    )
    add("")

    add("## 기록 건전성")
    add("")
    loss_detail = " · ".join(f"{kind} {count}" for kind, count in data.loss_by_kind)
    add(f"- 손실(LossRecord): {data.loss_total}건 — {loss_detail}")
    add(f"- 부분 기록(capture_status=partial): {data.partial_capture_count}건")
    add(f"- amendment: {data.amendment_count}건 (test 강등 {data.demotion_count}건)")
    add(
        f"- 손상 줄: {RECORDS_FILENAME} {data.corrupt_line_count}건 · "
        f"{CALIBRATION_FILENAME} {data.calibration_corrupt_count}건"
    )
    add("")

    add("## drift와 post-remove")
    add("")
    add(f"- drift 표시 사건: {data.drift_count}건 — 기록 시점 구현물 해시 ≠ 등록 spec 참조")
    add(
        f"- post-remove 발동: {data.post_remove_count}건 — remove 결정 뒤 같은 가드의 "
        "발동(append 순서 파생 판별). 결정 대비 실집행 불일치로 읽는다."
    )
    add("")

    add("## 가드별 현황")
    add("")
    if not data.guards:
        add("등록된 가드 없음.")
        add("")
    for row in data.guards:
        add(f"### {row.guard_id} — project {row.project} · 최신 v{row.latest_version}")
        add("")
        add(
            f"- 운영 세션 수: {row.operation_session_count} · 판정 가능 세션 수: "
            f"{row.decidable_session_count} → 판정 가능 가드: "
            f"{'예' if row.guard_decidable else '아니오'}"
        )
        add(
            f"- 사건(유효 출처): operation {row.operation_events} · "
            f"test {row.test_events} · unknown {row.unknown_events}"
        )
        add(
            f"- 결정: {row.decision_count}건 — 가치 검증 산입 "
            f"{row.countable_decision_count}건 · 발동 사건 없는 가드 결정 "
            f"{row.no_event_decision_count}건 (별도 표기, 지표 밖)"
        )
        add(
            f"- post-remove 발동: {row.post_remove_count}건 · drift 표시: "
            f"{row.drift_count}건 · 구현물 대조: {row.enforcement_status}"
        )
        if row.new_during_observation:
            add("- [관측 도중 추가된 신규 가드 — 사건을 기존 지표와 섞어 읽지 않는다]")
        add("")
    if data.new_guard_ids:
        marked = ", ".join(data.new_guard_ids)
        add(f"관측 도중 추가된 신규 가드: {marked} — 위 각 가드 절에 별도 표기했다.")
    else:
        add("관측 도중 추가된 신규 가드: 없음.")
    add("")

    add("## 기준선 측정")
    add("")
    if data.baseline_lines:
        for line in data.baseline_lines:
            add(f"- {line}")
    else:
        if data.baseline_note:
            add(f"- {data.baseline_note}")
        add(
            "- 미측정 — 첫 자연 operation 사건에서 기준선 측정(도구 없이 복원한 "
            f"시간·복원도 대 도구 경로)을 먼저 수행한다 ({OBSERVATION_PROTOCOL_REF} "
            "\"기준선 측정 절차\")."
        )
    add("")

    add("## 판정기 교정 상태")
    add("")
    if data.calibration_rows:
        add(
            f"- 교정 레코드: {data.calibration_record_count}건 ({CALIBRATION_FILENAME}) — "
            "설정 조합별 최신 상태:"
        )
        for row in sorted(
            data.calibration_rows, key=lambda r: (r.guard_label, r.model_id)
        ):
            status = "통과" if row.passed else "미통과"
            rubric_note = "현재 루브릭과 일치" if row.current_rubric else "다른 루브릭"
            add(
                f"  - {row.guard_label} · 모델 {row.model_id}: {status} "
                f"({row.examples_passed}/{row.examples_total}) · {rubric_note}"
            )
    else:
        add(
            f"- 교정 레코드 없음 ({CALIBRATION_FILENAME}) — 교정 없이 내린 판정에는 "
            "reason에 그 사실이 병기된다."
        )
    add("")

    add(f"## 기술 검증 — {TEST_EVIDENCE_MARK}")
    add("")
    add(
        f"- 시험(test) 사건: {data.test_count}건 — 위 운영 지표의 분자·분모에 "
        "포함되지 않는다 (운영 지표 산입 시험 사건 0건)."
    )
    if data.test_events_by_guard:
        detail = " · ".join(
            f"{guard_id} {count}건" for guard_id, count in data.test_events_by_guard
        )
        add(f"- 가드별 시험 사건: {detail}")
    add(
        f"- 이 절의 증거는 {TEST_EVIDENCE_ONLY_MARK}다 — 기술 흐름 재현의 증거일 뿐, "
        "사용자 가치 완료를 뜻하지 않으며 자연 operation 사건을 대체할 수 없다."
    )
    add("")

    add("## 한계와 주장 제한")
    add("")
    add(
        "- 단측 증거 한계: 발동한 사건만 관측하며, 가드가 막지 못한 실패(미발동)는 "
        "관측 밖이다."
    )
    add(
        "- 1인 자기측정 한계: 출처(origin)는 운영자 본인 선언에 기반하며 독립 검증 "
        "신호가 없다."
    )
    add(
        "- 판정기 한계: LLM-사용자 불일치율을 해석할 때 판정기 오류 가능성을 함께 "
        "고려한다 (\"판정기 교정 상태\" 절 참조)."
    )
    add(
        "- 소표본: 모든 비율은 원수 N/D와 함께만 읽는다. 분모가 작을수록 백분율을 "
        "과해석하지 않는다."
    )
    add(
        "- 관측 범위: Claude Code 세션만 관측한다. Codex 세션의 발동은 관측 밖이다 "
        f"({OBSERVATION_PROTOCOL_REF})."
    )
    add(
        f"- 관찰 종료 조건: {OBSERVATION_PROTOCOL_REF}의 종료 조건(관측창 4주 경과 "
        "또는 판정 가능 가드 1개 성립 중 먼저)을 따른다."
    )
    add(
        "- 이 보고서는 개인 도구 실험의 로컬 증거이며, 외부 사용자 실제 사용 전에는 "
        "다른 개발자·팀·시장으로 일반화하지 않는다."
    )

    text = "\n".join(lines) + "\n"
    # 마지막 방어선 — 어떤 경로로도 홈 경로 문자열이 남지 않게 한다 (spec §4).
    return text.replace(str(Path.home()), "~")


def generate_report(store: AppendStore, *, now: datetime | None = None) -> str:
    return render_report(build_report(store, now=now))


def default_report_path(store: AppendStore, *, now: datetime | None = None) -> Path:
    now = now if now is not None else datetime.now(timezone.utc)
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return store.root / REPORTS_DIRNAME / f"report-{stamp}.md"
