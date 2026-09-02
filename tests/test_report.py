"""보고서 계약 (T6, spec §6).

- 대표 지표·진단 지표는 원수 `N/D`와 백분율을 병기하고, 분모 0은 `미검증`이다.
- 판정 가능 정의는 metrics의 단일 정의를 재사용한다 (분자⊆분모 구성 강제).
- spec §6의 병기 목록 전부가 렌더링에 존재해야 한다.
- 시험 사건은 운영 지표 밖 — "기술 검증" 절에만 별도 표시.
- 보고서에 홈 경로·세션 식별자가 존재하지 않는다 (경로는 store 상대 표기).
"""

from __future__ import annotations

import json
import re
from datetime import timedelta
from pathlib import Path

import pytest

from rejectbench import (
    SCHEMA_VERSION,
    Amendment,
    AppendStore,
    EnforcementRef,
    JudgeCalibration,
    LossKind,
    LossRecord,
    Origin,
    OriginEvidence,
    append_calibration,
    value_hash,
)
from rejectbench.judge import CALIBRATION_FILENAME
from rejectbench.records import SchemaError
from rejectbench.judge import DEFAULT_MODEL_ID
from rejectbench.report import (
    BASELINE_FILENAME,
    UNPARTITIONED_PROJECT,
    PARTITION_OTHER,
    PARTITION_TOOL_DEVELOPMENT,
    REPORTS_DIRNAME,
    REPRESENTATIVE_PARTITION_MARK,
    TEST_EVIDENCE_MARK,
    TOOL_DEVELOPMENT_PROJECT,
    Ratio,
    build_report,
    default_report_path,
    generate_report,
    render_report,
)
from rejectbench.records import CaptureStatus, Decision, Utility, Verdict
from rejectbench.rubric import rubric_hash
from tests.factories import (
    make_decision,
    make_event,
    make_review,
    make_spec,
    make_verdict,
    ts,
)

NOW = ts(2000)
HOME_HINT = str(Path.home() / ".claude" / "hooks" / "ghost-guard.sh")


@pytest.fixture()
def store(tmp_path) -> AppendStore:
    return AppendStore(tmp_path / "store")


def build_rich_store(store: AppendStore) -> None:
    """guard-a(판정 가능·결정 완료), guard-b(발동 사건 없음), guard-c(관측 도중 추가).

    사건 구성 — 유효 출처 기준:
    - operation 5: ev-1(correct+useful), ev-2(incorrect+useful), ev-3(correct+unnecessary),
      ev-4(insufficient 보류 + 검토 미처리), ev-c(판정 미처리 + uncertain 보류)
    - test 2: ev-t(명시 플래그, drift), ev-d(강등 amendment)
    - unknown 1: ev-u(partial)
    - unregistered 1: ev-x (홈 경로 guard_hint — 렌더링 비노출 검증용)
    """
    spec_a = make_spec()  # guard-a v1 — 첫 사건 전 등록
    spec_b = make_spec(guard_id="guard-b", purpose="발동 사건 없는 가드")
    store.append(spec_a)
    store.append(spec_b)

    e1 = make_event(spec_a, event_id="ev-1", session_id="claude:s-1", occurred_at=ts(10))
    e2 = make_event(spec_a, event_id="ev-2", session_id="claude:s-2", occurred_at=ts(20))
    e3 = make_event(spec_a, event_id="ev-3", session_id="claude:s-1", occurred_at=ts(30))
    e4 = make_event(spec_a, event_id="ev-4", session_id="claude:s-3", occurred_at=ts(40))
    et = make_event(
        spec_a,
        event_id="ev-t",
        session_id="claude:s-t",
        origin=Origin.TEST,
        origin_evidence=OriginEvidence.EXPLICIT_FLAG,
        drift=True,
        occurred_at=ts(45),
    )
    ed = make_event(spec_a, event_id="ev-d", session_id="claude:s-1", occurred_at=ts(50))
    eu = make_event(
        spec_a,
        event_id="ev-u",
        session_id="claude:s-u",
        origin=Origin.UNKNOWN,
        origin_evidence=OriginEvidence.NO_CONTEXT,
        capture_status=CaptureStatus.PARTIAL,
        occurred_at=ts(55),
    )
    ex = make_event(
        None,
        event_id="ev-x",
        session_id="claude:s-x",
        unregistered=True,
        guard_hint=HOME_HINT,
        occurred_at=ts(58),
    )
    for event in (e1, e2, e3, e4, et, ed, eu, ex):
        store.append(event)

    store.append(make_verdict(e1, verdict_id="vd-1"))
    store.append(make_verdict(e2, verdict_id="vd-2", verdict=Verdict.INCORRECT_BLOCK))
    store.append(make_verdict(e3, verdict_id="vd-3"))
    store.append(
        make_verdict(
            e4, verdict_id="vd-4", verdict=Verdict.INSUFFICIENT_CONTEXT, judged_at=ts(70)
        )
    )
    store.append(make_review(e1, review_id="rv-1"))
    store.append(make_review(e2, review_id="rv-2"))
    store.append(make_review(e3, review_id="rv-3", utility=Utility.UNNECESSARY))

    # ev-d: operation → test 강등 amendment
    store.append(
        Amendment(
            amendment_id="am-1",
            target_id="ev-d",
            field="origin",
            previous_value_hash=value_hash("operation"),
            new_value="test",
            reason="시험 발동으로 확인",
            amended_at=ts(95),
        )
    )
    store.append(
        LossRecord(
            loss_id="loss-1",
            recorded_at=ts(96),
            kind=LossKind.VERDICT_FAILURE,
            detail="policy_verdict 실패: TransportError",
            subject_ref="ev-4",
        )
    )
    # 결정: guard-a keep(산입), guard-b keep(발동 사건 없음 — 별도 표기)
    store.append(make_decision(evidence_event_ids=("ev-1", "ev-2")))
    store.append(make_decision(guard_id="guard-b", decision_id="dc-2"))

    # guard-c: 첫 사건 뒤 등록 — 관측 도중 추가된 신규 가드
    spec_c = make_spec(
        guard_id="guard-c",
        purpose="관측 도중 추가된 가드",
        enforcement_ref=EnforcementRef(script_path=HOME_HINT, file_hash="sha256:" + "9" * 64),
    )
    store.append(spec_c)
    ec = make_event(
        spec_c, event_id="ev-c", session_id="claude:s-9", occurred_at=ts(1910)
    )
    store.append(ec)
    store.append(
        make_review(ec, review_id="rv-c", utility=Utility.UNCERTAIN, reviewed_at=ts(1990))
    )


class TestRatio:
    def test_zero_denominator_is_unverified_not_success(self):
        ratio = Ratio(numerator=0, denominator=0)
        assert ratio.unverified
        assert ratio.percentage is None
        assert ratio.render() == "미검증 (분모 0 — 성공 아님)"

    def test_renders_raw_fraction_with_percentage(self):
        assert Ratio(numerator=1, denominator=3).render() == "1/3 (33.3%)"
        assert Ratio(numerator=2, denominator=2).render() == "2/2 (100.0%)"

    def test_numerator_must_be_subset_of_denominator(self):
        with pytest.raises(SchemaError):
            Ratio(numerator=2, denominator=1)


class TestBuildReportMetrics:
    def test_headline_completion_reuses_metrics_definition(self, store):
        build_rich_store(store)
        data = build_report(store, now=NOW)
        assert data.completion.fraction == "1/1"
        assert data.completion.percentage == 100.0

    def test_diagnostic_ratios(self, store):
        build_rich_store(store)
        data = build_report(store, now=NOW)
        # 정책 판정 확정 operation 사건: ev-1, ev-2, ev-3 — incorrect: ev-2
        assert data.policy_mismatch.fraction == "1/3"
        # 유용성 검토 확정: ev-1, ev-2, ev-3 — unnecessary: ev-3
        assert data.unnecessary_block.fraction == "1/3"
        # 두 판단 확정: 3 — 방향 불일치: ev-2(그른 차단+유용), ev-3(옳은 차단+불필요)
        assert data.disagreement.fraction == "2/3"
        assert data.correct_but_unnecessary.fraction == "1/3"
        assert data.incorrect_but_useful.fraction == "1/3"

    def test_origin_counts_exclude_test_unknown_unregistered(self, store):
        build_rich_store(store)
        data = build_report(store, now=NOW)
        assert data.operation_count == 5
        assert data.test_count == 2  # 명시 플래그 1 + 강등 1
        assert data.unknown_count == 1
        assert data.unregistered_count == 1

    def test_pending_and_held_with_max_elapsed(self, store):
        build_rich_store(store)
        data = build_report(store, now=NOW)
        assert data.verdict_pending.unprocessed == 1  # ev-c
        assert data.verdict_pending.unprocessed_max_elapsed == timedelta(minutes=90)
        assert data.verdict_pending.held == 1  # ev-4 insufficient_context
        assert data.verdict_pending.held_max_elapsed == NOW - ts(70)
        assert data.review_pending.unprocessed == 1  # ev-4
        assert data.review_pending.unprocessed_max_elapsed == NOW - ts(40)
        assert data.review_pending.held == 1  # ev-c uncertain
        assert data.review_pending.held_max_elapsed == timedelta(minutes=10)

    def test_loss_partial_amendment_demotion_drift(self, store):
        build_rich_store(store)
        data = build_report(store, now=NOW)
        assert data.loss_total == 1
        assert dict(data.loss_by_kind)["verdict_failure"] == 1
        assert data.partial_capture_count == 1
        assert data.amendment_count == 1
        assert data.demotion_count == 1
        assert data.drift_count == 1
        assert data.post_remove_count == 0

    def test_guard_rows_and_new_guard_flag(self, store):
        build_rich_store(store)
        data = build_report(store, now=NOW)
        rows = {row.guard_id: row for row in data.guards}
        assert set(rows) == {"guard-a", "guard-b", "guard-c"}
        a = rows["guard-a"]
        assert a.operation_session_count == 3  # s-1, s-2, s-3
        assert a.guard_decidable
        assert a.decision_count == 1 and a.countable_decision_count == 1
        assert not a.new_during_observation
        b = rows["guard-b"]
        assert b.no_event_decision_count == 1
        c = rows["guard-c"]
        assert c.new_during_observation
        assert data.new_guard_ids == ("guard-c",)

    def test_post_remove_uses_derived_function_not_stored_flag(self, store):
        spec = make_spec()
        store.append(spec)
        e1 = make_event(spec, event_id="ev-1", session_id="claude:s-1")
        store.append(e1)
        store.append(
            make_decision(decision_id="dc-r", decision=Decision.REMOVE, rationale="제거")
        )
        # remove 결정 뒤 발동 — 레코드의 post_remove 저장값은 False다.
        after = make_event(spec, event_id="ev-2", session_id="claude:s-2", occurred_at=ts(200))
        assert after.post_remove is False
        store.append(after)
        data = build_report(store, now=NOW)
        assert data.post_remove_count == 1


class TestRenderReport:
    def test_empty_store_reports_operation_unverified(self, store):
        report = generate_report(store, now=NOW)
        assert "운영: 미검증" in report
        assert "미검증 (분모 0 — 성공 아님)" in report
        assert "operation 0 · test 0 · unknown 0 · unregistered 0" in report
        assert TEST_EVIDENCE_MARK in report
        assert "test evidence only" in report
        assert "미측정" in report  # 기준선
        assert "교정 레코드 없음" in report
        # 한계 문구 (spec §6·§9)
        assert "단측 증거" in report
        assert "1인 자기측정" in report
        assert "docs/관찰-프로토콜.md" in report
        assert "일반화하지 않는다" in report

    def test_rich_store_renders_raw_numbers_and_directions(self, store):
        build_rich_store(store)
        report = generate_report(store, now=NOW)
        assert "1/1 (100.0%)" in report  # 대표 지표
        assert "운영: 미검증" not in report
        assert report.count("1/3 (33.3%)") >= 2  # 정책 불일치율·불필요 차단율
        assert "2/3 (66.7%)" in report  # 불일치율
        assert "정책상 옳은 차단 + 사용자 불필요: 1/3" in report
        assert "정책상 그른 차단 + 사용자 유용: 1/3" in report
        assert "operation 5 · test 2 · unknown 1 · unregistered 1" in report

    def test_rich_store_renders_pending_elapsed(self, store):
        build_rich_store(store)
        report = generate_report(store, now=NOW)
        assert "1시간 30분" in report  # verdict 미처리 최장 경과 (ev-c)
        assert "1일 8시간" in report  # 보류 최장 경과 (ev-4)

    def test_new_guard_marked_separately(self, store):
        build_rich_store(store)
        report = generate_report(store, now=NOW)
        assert "관측 도중 추가" in report
        assert "guard-c" in report

    def test_test_events_only_in_technical_section(self, store):
        build_rich_store(store)
        report = generate_report(store, now=NOW)
        assert TEST_EVIDENCE_MARK in report
        assert "시험(test) 사건: 2건" in report

    def test_no_home_path_or_session_ids(self, store):
        build_rich_store(store)
        report = generate_report(store, now=NOW)
        assert str(Path.home()) not in report
        assert "/Users/" not in report
        assert "claude:" not in report  # 세션 식별자 비노출 — 수는 숫자만
        assert str(store.root) not in report
        for event_id in ("ev-1", "ev-c", "ev-x"):
            assert event_id not in report


class TestBaselineAndCalibration:
    def test_baseline_absent_is_reported_as_unmeasured(self, store):
        build_rich_store(store)
        report = generate_report(store, now=NOW)
        assert "기준선" in report
        assert "미측정" in report

    def test_baseline_file_rendered(self, store):
        build_rich_store(store)
        store.root.mkdir(parents=True, exist_ok=True)
        (store.root / BASELINE_FILENAME).write_text(
            '{"복원 시간(분)": 12, "복원도": "4/5"}', encoding="utf-8"
        )
        report = generate_report(store, now=NOW)
        assert "복원 시간(분): 12" in report
        assert "복원도: 4/5" in report

    def test_calibration_sidecar_status(self, store):
        build_rich_store(store)
        spec = make_spec()
        current = rubric_hash()
        stale = JudgeCalibration(
            calibration_id="cal-1",
            calibrated_at=ts(100),
            guard_spec_hash=spec.content_hash,
            rubric_hash=current,
            model_id="judge-model-1",
            model_settings_hash="sha256:" + "3" * 64,
            examples_total=2,
            examples_passed=1,
            passed=False,
            failures=("allow[0]: 기대 incorrect_block, 실제 correct_block",),
        )
        fresh = JudgeCalibration(
            calibration_id="cal-2",
            calibrated_at=ts(110),
            guard_spec_hash=spec.content_hash,
            rubric_hash=current,
            model_id="judge-model-1",
            model_settings_hash="sha256:" + "3" * 64,
            examples_total=2,
            examples_passed=2,
            passed=True,
            failures=(),
        )
        append_calibration(store.root, stale)
        append_calibration(store.root, fresh)
        with open(store.root / CALIBRATION_FILENAME, "a", encoding="utf-8") as fh:
            fh.write("{broken json\n")
        report = generate_report(store, now=NOW)
        assert "guard-a v1" in report
        assert "통과 (2/2)" in report
        assert "교정 레코드 없음" not in report
        assert "손상" in report


class TestDefaultReportPath:
    def test_path_under_store_reports_with_utc_stamp(self, store):
        path = default_report_path(store, now=NOW)
        assert path.parent == store.root / REPORTS_DIRNAME
        assert re.fullmatch(r"report-\d{8}T\d{6}Z\.md", path.name)


# --- 003 §9 (E7) 보고서 파티션 — 도구개발 / 그-외 -------------------------------
#
# 운영 사건 집합(`operation_event_ids`)에서 파생되는 지표 전부를 시험/운영 분리 안쪽에서
# 다시 `GuardEvent.project == "reject-bench"`(도구개발) / 그 외로 가른다. 대표값은 그-외.
# 결정 완료율은 가드 단위라 두 벌의 합이 전체와 다를 수 있다 — 합산 검산을 넣지 않는다.

OTHER_PROJECT = "provenance"


def build_partitioned_store(store: AppendStore) -> None:
    """guard-a·guard-b에 도구개발/그-외 사건이 섞인 store.

    guard-a: ev-1(도구개발 s-1, correct+useful) ev-2(도구개발 s-2, 보류 verdict + useful)
             ev-3(그-외 s-o1, correct+unnecessary) ev-4(그-외 s-o2, incorrect+useful)
             ev-5(그-외 s-o1, verdict 미처리 + uncertain 보류) · keep 결정 근거 (ev-3, ev-4)
    guard-b: ev-6(도구개발 s-b1, correct+useful) ev-7(그-외 s-b2, correct+useful) · 결정 없음

    귀결 — 결정 완료율: 전체 1/2(guard-a 결정 완료, guard-b는 파티션을 가로질러서만
    판정 가능), 도구개발 0/0(guard-a는 ev-2 보류로 세션 1개, guard-b도 1개), 그-외 1/1.
    분모 합 0+1 ≠ 2 — 합산 검산이 성립하지 않는 픽스처다.
    """
    spec_a = make_spec()
    spec_b = make_spec(guard_id="guard-b", purpose="파티션을 가로지르는 가드")
    store.append(spec_a)
    store.append(spec_b)
    e1 = make_event(spec_a, event_id="ev-1", session_id="claude:s-1", occurred_at=ts(10))
    e2 = make_event(spec_a, event_id="ev-2", session_id="claude:s-2", occurred_at=ts(20))
    e3 = make_event(
        spec_a, event_id="ev-3", session_id="claude:s-o1", project=OTHER_PROJECT, occurred_at=ts(30)
    )
    e4 = make_event(
        spec_a, event_id="ev-4", session_id="claude:s-o2", project=OTHER_PROJECT, occurred_at=ts(40)
    )
    e5 = make_event(
        spec_a, event_id="ev-5", session_id="claude:s-o1", project=OTHER_PROJECT, occurred_at=ts(50)
    )
    e6 = make_event(spec_b, event_id="ev-6", session_id="claude:s-b1", occurred_at=ts(60))
    e7 = make_event(
        spec_b, event_id="ev-7", session_id="claude:s-b2", project=OTHER_PROJECT, occurred_at=ts(70)
    )
    for event in (e1, e2, e3, e4, e5, e6, e7):
        store.append(event)
    store.append(make_verdict(e1, verdict_id="vd-1"))
    store.append(
        make_verdict(e2, verdict_id="vd-2", verdict=Verdict.INSUFFICIENT_CONTEXT, judged_at=ts(80))
    )
    store.append(make_verdict(e3, verdict_id="vd-3"))
    store.append(make_verdict(e4, verdict_id="vd-4", verdict=Verdict.INCORRECT_BLOCK))
    store.append(make_verdict(e6, verdict_id="vd-6"))
    store.append(make_verdict(e7, verdict_id="vd-7"))
    store.append(make_review(e1, review_id="rv-1"))
    store.append(make_review(e2, review_id="rv-2"))
    store.append(make_review(e3, review_id="rv-3", utility=Utility.UNNECESSARY))
    store.append(make_review(e4, review_id="rv-4"))
    store.append(make_review(e5, review_id="rv-5", utility=Utility.UNCERTAIN, reviewed_at=ts(90)))
    store.append(make_review(e6, review_id="rv-6"))
    store.append(make_review(e7, review_id="rv-7"))
    store.append(make_decision(evidence_event_ids=("ev-3", "ev-4")))


class TestReportPartition:
    def test_partition_key_is_the_tool_development_project(self):
        assert TOOL_DEVELOPMENT_PROJECT == "reject-bench"

    def test_every_operation_derived_metric_has_three_values(self, store):
        build_partitioned_store(store)
        data = build_report(store, now=NOW)
        overall, tool, other = data.overall, data.tool_development, data.other
        assert (overall.label, tool.label, other.label) == ("전체", PARTITION_TOOL_DEVELOPMENT, PARTITION_OTHER)

        assert (overall.operation_count, tool.operation_count, other.operation_count) == (7, 3, 4)
        assert (overall.completion.fraction, tool.completion.fraction, other.completion.fraction) == (
            "1/2",
            "0/0",
            "1/1",
        )
        assert tool.completion.unverified and not other.completion.unverified
        assert (overall.policy_mismatch.fraction, tool.policy_mismatch.fraction, other.policy_mismatch.fraction) == (
            "1/5",
            "0/2",
            "1/3",
        )
        assert (
            overall.unnecessary_block.fraction,
            tool.unnecessary_block.fraction,
            other.unnecessary_block.fraction,
        ) == ("1/6", "0/3", "1/3")
        assert (overall.disagreement.fraction, tool.disagreement.fraction, other.disagreement.fraction) == (
            "2/5",
            "0/2",
            "2/3",
        )
        assert (other.correct_but_unnecessary.fraction, other.incorrect_but_useful.fraction) == ("1/3", "1/3")
        assert (tool.correct_but_unnecessary.fraction, tool.incorrect_but_useful.fraction) == ("0/2", "0/2")

        assert (overall.verdict_pending.unprocessed, tool.verdict_pending.unprocessed, other.verdict_pending.unprocessed) == (1, 0, 1)
        assert (overall.verdict_pending.held, tool.verdict_pending.held, other.verdict_pending.held) == (1, 1, 0)
        assert tool.verdict_pending.held_max_elapsed == NOW - ts(80)
        assert other.verdict_pending.held_max_elapsed is None
        assert (overall.review_pending.held, tool.review_pending.held, other.review_pending.held) == (1, 0, 1)
        assert other.review_pending.held_max_elapsed == NOW - ts(90)
        assert other.verdict_pending.unprocessed_max_elapsed == NOW - ts(50)

    def test_flat_fields_are_the_overall_values(self, store):
        build_partitioned_store(store)
        data = build_report(store, now=NOW)
        assert data.completion == data.overall.completion
        assert data.policy_mismatch == data.overall.policy_mismatch
        assert data.operation_count == data.overall.operation_count
        assert data.verdict_pending == data.overall.verdict_pending

    def test_completion_partitions_do_not_sum_to_the_total(self, store):
        """결정 완료율은 가드 단위다 — guard-b는 전체에서만 판정 가능하다. 합산 검산 금지."""
        build_partitioned_store(store)
        data = build_report(store, now=NOW)
        assert data.overall.completion.denominator == 2
        assert (
            data.tool_development.completion.denominator + data.other.completion.denominator
            != data.overall.completion.denominator
        )
        # 사건 단위 지표의 원수는 두 벌의 합이 전체다 — 결정 완료율만 예외라는 사실을 함께 고정.
        assert data.tool_development.operation_count + data.other.operation_count == data.overall.operation_count

    def test_numerator_subset_holds_inside_each_partition(self, store):
        build_partitioned_store(store)
        data = build_report(store, now=NOW)
        for metrics in (data.overall, data.tool_development, data.other):
            for ratio in (
                metrics.policy_mismatch,
                metrics.unnecessary_block,
                metrics.disagreement,
                metrics.correct_but_unnecessary,
                metrics.incorrect_but_useful,
            ):
                assert 0 <= ratio.numerator <= ratio.denominator
            assert metrics.completion.decided_guard_ids <= metrics.completion.decidable_guard_ids

    def test_render_shows_three_values_and_names_the_representative_partition(self, store):
        build_partitioned_store(store)
        report = generate_report(store, now=NOW)
        assert REPRESENTATIVE_PARTITION_MARK in report  # "대표값은 그-외" 문면
        assert "1/2 (50.0%)" in report  # 전체 완료율
        assert f"{PARTITION_TOOL_DEVELOPMENT} 미검증 (분모 0 — 성공 아님)" in report  # 분모 0 파티션
        assert f"{PARTITION_OTHER} 1/1 (100.0%)" in report
        assert f"{PARTITION_TOOL_DEVELOPMENT} 0/2 (0.0%)" in report
        assert f"{PARTITION_OTHER} 1/3 (33.3%)" in report
        assert f"operation 7 · test 0 · unknown 0 · unregistered 0" in report
        assert f"{PARTITION_TOOL_DEVELOPMENT} 3 · {PARTITION_OTHER} 4" in report
        # 대표(그-외) 상태 줄과 전체 상태 줄이 각각 있다.
        assert "운영: 증거 기반 결정 완료율 1/2 (50.0%)" in report
        assert f"대표값({PARTITION_OTHER}): 증거 기반 결정 완료율 1/1 (100.0%)" in report

    def test_subdirectory_cwd_of_this_repo_is_counted_as_other(self, store):
        """파티션 키는 cwd basename이다 — 하위 디렉터리 cwd는 그-외로 간다 (spec §9.1 한계).

        고치는 테스트가 아니라 한계를 고정하는 테스트다. 기록기가 저장소 식별로 바뀌면
        이 테스트가 먼저 깨져 spec §9.1의 한계 문면을 함께 고치게 한다.
        """
        spec_a = make_spec()
        store.append(spec_a)
        store.append(make_event(spec_a, event_id="ev-1", session_id="claude:s-1", project="docs"))
        store.append(
            make_event(spec_a, event_id="ev-2", session_id="claude:s-2", project="reject-bench-wt2")
        )
        data = build_report(store, now=NOW)
        assert (data.tool_development.operation_count, data.other.operation_count) == (0, 2)
        report = generate_report(store, now=NOW)
        assert "파티션 키의 한계" in report

    def test_partition_completion_is_measured_on_evidence_inside_the_partition(self, store):
        """근거 ∩ 파티션으로 잰다 (spec §9.6, 프로토콜 ⑩) — 전부 인용한 결정이 그-외에서도 결정이다."""
        spec_a = make_spec()
        store.append(spec_a)
        e1 = make_event(spec_a, event_id="ev-1", session_id="claude:s-1")  # 도구개발
        e2 = make_event(spec_a, event_id="ev-2", session_id="claude:s-o1", project=OTHER_PROJECT)
        e3 = make_event(spec_a, event_id="ev-3", session_id="claude:s-o2", project=OTHER_PROJECT)
        for event in (e1, e2, e3):
            store.append(event)
            store.append(make_verdict(event, verdict_id=f"vd-{event.event_id}"))
            store.append(make_review(event, review_id=f"rv-{event.event_id}"))
        store.append(make_decision(evidence_event_ids=("ev-1", "ev-2", "ev-3")))
        data = build_report(store, now=NOW)
        assert data.overall.completion.fraction == "1/1"
        assert data.other.completion.fraction == "1/1"  # 근거 ∩ 그-외 = {ev-2, ev-3}, 2세션
        assert data.tool_development.completion.fraction == "0/0"  # 도구개발은 1세션이라 분모 밖

    def test_evidence_with_one_session_inside_the_partition_stays_undecided(self, store):
        """근거를 좁게 인용해도 파티션 안 근거가 1세션이면 미결정이다."""
        spec_a = make_spec()
        store.append(spec_a)
        e1 = make_event(spec_a, event_id="ev-1", session_id="claude:s-1")
        e2 = make_event(spec_a, event_id="ev-2", session_id="claude:s-o1", project=OTHER_PROJECT)
        e3 = make_event(spec_a, event_id="ev-3", session_id="claude:s-o2", project=OTHER_PROJECT)
        for event in (e1, e2, e3):
            store.append(event)
            store.append(make_verdict(event, verdict_id=f"vd-{event.event_id}"))
            store.append(make_review(event, review_id=f"rv-{event.event_id}"))
        store.append(make_decision(evidence_event_ids=("ev-1", "ev-2")))
        data = build_report(store, now=NOW)
        assert data.overall.completion.fraction == "1/1"
        assert data.other.completion.fraction == "0/1"

    def test_unknown_project_is_in_neither_partition_but_in_the_total(self, store):
        """`project == "unknown"`은 '다른 저장소' 증거가 아니다 — 파티션 밖, 전체 안 (spec §9.1)."""
        spec_a = make_spec()
        store.append(spec_a)
        store.append(make_event(spec_a, event_id="ev-1", session_id="claude:s-1"))
        store.append(
            make_event(spec_a, event_id="ev-2", session_id="claude:s-2", project=UNPARTITIONED_PROJECT)
        )
        data = build_report(store, now=NOW)
        assert data.overall.operation_count == 2
        assert (data.tool_development.operation_count, data.other.operation_count) == (1, 0)
        assert data.unpartitioned_count == 1
        report = generate_report(store, now=NOW)
        assert f'파티션 불가(`project == "{UNPARTITIONED_PROJECT}"`) 1' in report

    def test_report_shows_latest_verdict_model_distribution(self, store):
        """확정값의 모델 혼합과 순서 위반은 보고서가 드러낸다 (프로토콜 ⑪)."""
        spec_a = make_spec()
        store.append(spec_a)
        e1 = make_event(spec_a, event_id="ev-1", session_id="claude:s-1")
        e2 = make_event(spec_a, event_id="ev-2", session_id="claude:s-2")
        e3 = make_event(spec_a, event_id="ev-3", session_id="claude:s-3")
        for event in (e1, e2, e3):
            store.append(event)
        store.append(make_verdict(e1, verdict_id="vd-1", model_id=DEFAULT_MODEL_ID))
        store.append(make_verdict(e2, verdict_id="vd-2", model_id="other-model"))
        store.append(make_verdict(e2, verdict_id="vd-2b", model_id=DEFAULT_MODEL_ID))  # 기본이 마지막
        store.append(make_verdict(e3, verdict_id="vd-3", model_id="other-model"))
        data = build_report(store, now=NOW)
        assert data.verdict_model_counts == ((DEFAULT_MODEL_ID, 2), ("other-model", 1))
        report = generate_report(store, now=NOW)
        assert f"{DEFAULT_MODEL_ID} 2 · other-model 1 — 기본({DEFAULT_MODEL_ID}) 이외 1건" in report

    def test_insufficient_context_threshold_uses_judged_events_as_denominator(self, store):
        """임계 ⑤의 분모는 판정 레코드가 있는 운영 사건(확정+보류)이다 — 보류값을 뺀 분모는 정의상 0이 된다."""
        spec_a = make_spec()
        store.append(spec_a)
        for index in range(1, 7):
            event = make_event(spec_a, event_id=f"ev-{index}", session_id=f"claude:s-{index}")
            store.append(event)
            if index <= 5:
                verdict = Verdict.INSUFFICIENT_CONTEXT if index <= 3 else Verdict.CORRECT_BLOCK
                store.append(make_verdict(event, verdict_id=f"vd-{index}", verdict=verdict))
        data = build_report(store, now=NOW)
        assert data.insufficient_context_ratio.fraction == "3/5"  # ev-6은 미처리라 분모 밖
        report = generate_report(store, now=NOW)
        assert "insufficient_context 비율(선등록 임계 ⑤, 전체 기준): 3/5 (60.0%)" in report
        assert "**발동 — 캡처 설계 실패 신호**" in report

    def test_rich_store_has_an_empty_other_partition_rendered_as_unverified(self, store):
        """기존 픽스처는 전부 도구개발이다 — 그-외는 분모 0이라 미검증이지 성공이 아니다."""
        build_rich_store(store)
        data = build_report(store, now=NOW)
        assert data.other.operation_count == 0
        assert data.other.completion.unverified
        assert data.tool_development.operation_count == data.overall.operation_count == 5
        report = generate_report(store, now=NOW)
        assert f"대표값({PARTITION_OTHER}): 미검증" in report
        assert "운영: 미검증" not in report  # 전체 상태 줄은 그대로 전체 값이다

    def test_non_operation_aggregates_stay_single_valued(self, store):
        """손실·정정·강등·손상 줄·총 레코드·출처 집계·기준선·교정은 파티션하지 않는다."""
        build_partitioned_store(store)
        report = generate_report(store, now=NOW)
        health = report.split("## 기록 건전성")[1].split("## drift")[0]
        assert PARTITION_TOOL_DEVELOPMENT not in health and PARTITION_OTHER not in health
        guards = report.split("## 가드별 현황")[1].split("## 기준선")[0]
        # 가드별 표는 기존 project 열이 판별자다 — 열을 중복 추가하지 않는다.
        assert f"project {OTHER_PROJECT}" not in guards  # spec의 project는 reject-bench
        assert guards.count("project reject-bench") == 2
        assert PARTITION_TOOL_DEVELOPMENT not in guards and PARTITION_OTHER not in guards

    def test_no_home_path_or_session_ids_with_partitions(self, store):
        build_partitioned_store(store)
        report = generate_report(store, now=NOW)
        assert str(Path.home()) not in report
        assert "claude:" not in report
        for event_id in ("ev-1", "ev-5", "ev-7"):
            assert event_id not in report


class TestSchemaVersionHeader:
    def legacy_line(self, store: AppendStore, event) -> None:
        payload = event.to_json()
        del payload["session_id_format"]
        payload["schema_version"] = "7.0"
        with open(store.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n")

    def test_single_value_when_every_record_is_current(self, store):
        build_rich_store(store)
        data = build_report(store, now=NOW)
        assert data.schema_versions_present == (SCHEMA_VERSION,)
        report = generate_report(store, now=NOW)
        assert f"- 스키마 버전: {SCHEMA_VERSION}\n" in report
        assert "스냅샷 실존" not in report

    def test_empty_store_shows_the_recorder_version_only(self, store):
        data = build_report(store, now=NOW)
        assert data.schema_versions_present == ()
        assert f"- 스키마 버전: {SCHEMA_VERSION}\n" in generate_report(store, now=NOW)

    def test_mixed_store_lists_recorder_version_and_present_set(self, store):
        spec = make_spec()
        store.append(spec)
        store.append(make_event(spec, event_id="ev-new", session_id="claude:s-1"))
        self.legacy_line(store, make_event(spec, event_id="ev-old", session_id="claude:s-2", occurred_at=ts(5)))
        data = build_report(store, now=NOW)
        assert data.corrupt_line_count == 0  # 구형 줄은 손상 줄이 아니다 (E2 파서 완화)
        assert data.schema_versions_present == ("7.0", SCHEMA_VERSION)
        report = generate_report(store, now=NOW)
        assert f"- 스키마 버전: 기록기 현행 {SCHEMA_VERSION} · 스냅샷 실존 7.0, {SCHEMA_VERSION}\n" in report
