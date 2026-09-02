"""스키마 계약: 전역 규칙(스키마 버전·고유 id·UTC 시각), enum, 필수 필드, 직렬화."""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone

import pytest

from rejectbench import (
    SCHEMA_VERSION,
    SESSION_ID_RAW_RULE,
    ActionSummary,
    Amendment,
    AppendStore,
    CaptureStatus,
    Decision,
    GuardDecision,
    GuardEvent,
    GuardSpec,
    JudgeCalibration,
    LossKind,
    LossRecord,
    Origin,
    OriginEvidence,
    SchemaError,
    SessionIdFormat,
    Utility,
    Verdict,
    record_from_json,
    session_id_format,
    split_session_id,
)
from tests.factories import (
    make_action,
    make_decision,
    make_event,
    make_review,
    make_spec,
    make_verdict,
    ts,
)


def make_loss(**kwargs) -> LossRecord:
    fields = {
        "loss_id": "loss-1",
        "recorded_at": ts(5),
        "kind": LossKind.WRITE_FAILURE,
        "detail": "주 저장 append 실패",
        "subject_ref": "ev-1",
    }
    fields.update(kwargs)
    return LossRecord(**fields)


def make_amendment(**kwargs) -> Amendment:
    fields = {
        "amendment_id": "am-1",
        "target_id": "ev-1",
        "field": "origin",
        "previous_value_hash": "sha256:" + "a" * 64,
        "new_value": "test",
        "reason": "검토에서 강제 발동으로 확인",
        "amended_at": ts(100),
    }
    fields.update(kwargs)
    return Amendment(**fields)


def all_records():
    spec = make_spec()
    event = make_event(spec)
    return [
        spec,
        event,
        make_verdict(event),
        make_review(event),
        make_decision(evidence_event_ids=(event.event_id,)),
        make_loss(),
        make_amendment(),
    ]


class TestGlobalRules:
    def test_every_record_has_schema_version_unique_id_and_utc_time(self):
        for record in all_records():
            assert record.schema_version == SCHEMA_VERSION
            assert isinstance(record.record_id, str) and record.record_id
            t = record.record_time
            assert t.tzinfo is not None
            assert t.utcoffset().total_seconds() == 0

    def test_naive_datetime_rejected(self):
        naive = datetime(2026, 8, 1, 12, 0, 0)
        with pytest.raises(SchemaError):
            make_spec(created_at=naive)
        with pytest.raises(SchemaError):
            make_event(make_spec(), occurred_at=naive)

    def test_non_utc_timezone_rejected(self):
        from datetime import timedelta

        kst = timezone(timedelta(hours=9))
        with pytest.raises(SchemaError):
            make_spec(created_at=datetime(2026, 8, 1, 12, 0, tzinfo=kst))

    def test_empty_id_rejected(self):
        with pytest.raises(SchemaError):
            make_event(make_spec(), event_id="")


class TestEnums:
    def test_verdict_enum_is_exactly_the_spec_set(self):
        assert {v.value for v in Verdict} == {
            "correct_block",
            "incorrect_block",
            "insufficient_context",
        }

    def test_utility_enum(self):
        assert {u.value for u in Utility} == {"useful", "unnecessary", "uncertain"}

    def test_decision_enum(self):
        assert {d.value for d in Decision} == {"keep", "modify", "remove"}

    def test_origin_enum(self):
        assert {o.value for o in Origin} == {"operation", "test", "unknown"}

    def test_capture_status_enum(self):
        assert {c.value for c in CaptureStatus} == {"complete", "partial"}

    def test_invalid_enum_value_rejected_on_parse(self):
        payload = make_verdict(make_event(make_spec())).to_json()
        payload["verdict"] = "maybe_block"
        with pytest.raises(SchemaError):
            record_from_json(payload)


class TestGuardEvent:
    def test_registered_event_requires_full_guard_reference(self):
        spec = make_spec()
        with pytest.raises(SchemaError):
            make_event(spec, guard_spec_hash=None)
        with pytest.raises(SchemaError):
            make_event(spec, guard_version=None)

    def test_unregistered_event_replaces_reference_with_marker_and_hint(self):
        event = make_event(
            None,
            unregistered=True,
            guard_hint="~/.claude/hooks/unknown-guard.sh",
        )
        assert event.unregistered is True
        assert event.guard_id is None
        assert event.guard_version is None
        assert event.guard_spec_hash is None

    def test_unregistered_event_requires_hint(self):
        with pytest.raises(SchemaError):
            make_event(None, unregistered=True)

    def test_unregistered_event_forbids_guard_reference(self):
        spec = make_spec()
        with pytest.raises(SchemaError):
            make_event(spec, unregistered=True, guard_hint="x.sh")

    def test_event_without_reference_and_without_marker_rejected(self):
        with pytest.raises(SchemaError):
            make_event(None)

    def test_drift_and_post_remove_flags_exist_and_default_false(self):
        spec = make_spec()
        event = make_event(spec)
        assert event.drift is False
        assert event.post_remove is False
        flagged = make_event(spec, drift=True, post_remove=True)
        assert flagged.drift is True
        assert flagged.post_remove is True

    def test_partial_capture_status_allowed(self):
        event = make_event(make_spec(), capture_status=CaptureStatus.PARTIAL)
        assert event.capture_status is CaptureStatus.PARTIAL

    def test_origin_evidence_pairing_enforced(self):
        spec = make_spec()
        # 결정표에 없는 조합은 스키마 수준에서 거부한다.
        with pytest.raises(SchemaError):
            make_event(spec, origin=Origin.OPERATION, origin_evidence=OriginEvidence.EXPLICIT_FLAG)
        with pytest.raises(SchemaError):
            make_event(spec, origin=Origin.TEST, origin_evidence=OriginEvidence.DEFAULT_INHERITED)
        with pytest.raises(SchemaError):
            make_event(spec, origin=Origin.UNKNOWN, origin_evidence=OriginEvidence.DEFAULT_INHERITED)
        make_event(spec, origin=Origin.TEST, origin_evidence=OriginEvidence.EXPLICIT_FLAG)
        make_event(spec, origin=Origin.UNKNOWN, origin_evidence=OriginEvidence.NO_CONTEXT)

    def test_schema_has_no_full_text_fields(self):
        # 파일 내용·프롬프트·전체 명령·도구 응답 전문을 담을 자리가 없어야 한다.
        field_names = {f.name for f in dataclasses.fields(GuardEvent)}
        assert field_names == {
            "event_id",
            "occurred_at",
            "session_id",
            "project",
            "action",
            "reason",
            "origin",
            "origin_evidence",
            "capture_status",
            "guard_id",
            "guard_version",
            "guard_spec_hash",
            "unregistered",
            "guard_hint",
            "drift",
            "post_remove",
            "session_id_format",
            "schema_version",
        }


# --- 세션 ID 적재 형식 (003 spec §4) -------------------------------------------
#
# 계약이 고정한 파라미터(8~128자, `[A-Za-z0-9_-]`)는 리터럴로 못 박는다 — 구현
# 상수를 읽어 비교하면 튜닝해도 테스트가 따라 움직여 계약 개정 없이 지나간다.

UUID_RAW = "11111111-2222-3333-4444-555555555555"


class TestSessionIdDecomposition:
    def test_everything_after_the_first_colon_is_the_raw_part(self):
        assert split_session_id(f"claude:{UUID_RAW}") == ("claude", UUID_RAW)
        assert split_session_id("claude:abc:def") == ("claude", "abc:def")

    def test_value_without_colon_has_no_raw_part(self):
        assert split_session_id("claude") == ("claude", None)

    def test_empty_raw_part_is_present_but_empty(self):
        assert split_session_id("claude:") == ("claude", "")


class TestSessionIdFormat:
    def test_format_enum_is_exactly_the_spec_set(self):
        assert {f.value for f in SessionIdFormat} == {
            "conforming",
            "nonconforming",
            "unchecked",
        }

    def test_uuid_raw_part_conforms(self):
        assert session_id_format(f"claude:{UUID_RAW}") is SessionIdFormat.CONFORMING

    def test_length_bounds_are_8_and_128_inclusive(self):
        assert session_id_format("claude:" + "a" * 7) is SessionIdFormat.NONCONFORMING
        assert session_id_format("claude:" + "a" * 8) is SessionIdFormat.CONFORMING
        assert session_id_format("claude:" + "a" * 128) is SessionIdFormat.CONFORMING
        assert session_id_format("claude:" + "a" * 129) is SessionIdFormat.NONCONFORMING

    @pytest.mark.parametrize(
        "raw",
        [
            "abcdefg.h",  # 점
            "abcd efgh",  # 공백
            "abcd/efgh",  # 경로 구분자
            "abcd:efgh",  # 두 번째 콜론도 원본의 일부라 금지 문자다
            "abcdefgh\n",  # 끝 개행 — `$` 앵커는 이걸 놓친다
            "abcdefghé",  # 비ASCII
        ],
    )
    def test_forbidden_characters_do_not_conform(self, raw):
        assert session_id_format(f"claude:{raw}") is SessionIdFormat.NONCONFORMING

    def test_hyphen_and_underscore_are_in_the_charset(self):
        # 하드 게이트: Claude Code 세션 ID가 UUID(하이픈 포함)라 하이픈이 빠지면 안 된다.
        assert session_id_format("claude:abcd-efgh") is SessionIdFormat.CONFORMING
        assert session_id_format("claude:abcd_efgh") is SessionIdFormat.CONFORMING

    def test_value_without_colon_does_not_conform(self):
        assert session_id_format(UUID_RAW) is SessionIdFormat.NONCONFORMING

    def test_a_uuid_after_a_second_colon_does_not_conform(self):
        # 첫 `:` 이후 **전부**가 원본이다 — 마지막 `:` 기준 분해면 이 값이 준수가 된다.
        assert session_id_format(f"codex:run:{UUID_RAW}") is SessionIdFormat.NONCONFORMING

    def test_only_the_raw_part_is_checked(self):
        # harness 부분은 검사 대상이 아니다 — 술어를 어기는 접두라도 원본이 준수면 준수.
        assert session_id_format(f"we ird:{UUID_RAW}") is SessionIdFormat.CONFORMING

    def test_rule_parameters_are_one_named_constant(self):
        assert SESSION_ID_RAW_RULE.min_len == 8
        assert SESSION_ID_RAW_RULE.max_len == 128
        assert SESSION_ID_RAW_RULE.charset == "A-Za-z0-9_-"


class TestSessionIdFormatField:
    def test_event_carries_one_of_three_states_and_null_is_rejected(self):
        event = make_event(make_spec(), session_id_format=SessionIdFormat.CONFORMING)
        assert event.session_id_format is SessionIdFormat.CONFORMING
        with pytest.raises(SchemaError):
            make_event(make_spec(), session_id_format=None)
        with pytest.raises(SchemaError):
            make_event(make_spec(), session_id_format="conforming")

    def test_serialized_event_carries_the_format_string(self):
        payload = make_event(
            make_spec(), session_id_format=SessionIdFormat.NONCONFORMING
        ).to_json()
        assert payload["session_id_format"] == "nonconforming"
        assert record_from_json(payload).session_id_format is SessionIdFormat.NONCONFORMING

    def test_invalid_format_string_rejected_on_parse(self):
        payload = make_event(make_spec()).to_json()
        payload["session_id_format"] = "maybe"
        with pytest.raises(SchemaError):
            record_from_json(payload)
        payload["session_id_format"] = None
        with pytest.raises(SchemaError):
            record_from_json(payload)


class TestSchemaVersion:
    def test_schema_version_is_7_1(self):
        assert SCHEMA_VERSION == "7.1"

    def test_all_seven_records_and_the_calibration_sidecar_follow_the_global_constant(self):
        for record in all_records():
            assert record.schema_version == "7.1"
        (field,) = [f for f in dataclasses.fields(JudgeCalibration) if f.name == "schema_version"]
        assert field.default == "7.1"


def legacy_event_payload() -> dict:
    """7.0 구형 저장 JSON — `session_id_format` 키가 없다."""
    payload = make_event(make_spec()).to_json()
    del payload["session_id_format"]
    payload["schema_version"] = "7.0"
    return payload


def append_raw_line(store: AppendStore, payload: dict) -> None:
    store.root.mkdir(parents=True, exist_ok=True)
    with open(store.path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n")


class TestLegacyEventParsing:
    def test_7_0_event_without_the_field_loads_as_unchecked(self):
        event = record_from_json(legacy_event_payload())
        assert isinstance(event, GuardEvent)
        assert event.session_id_format is SessionIdFormat.UNCHECKED
        assert event.schema_version == "7.0"  # 재작성하지 않는다 — 저장값 그대로

    def test_relaxation_keys_on_field_absence_not_on_the_version_value(self):
        payload = legacy_event_payload()
        payload["schema_version"] = "7.1"
        assert record_from_json(payload).session_id_format is SessionIdFormat.UNCHECKED

    def test_parser_does_not_mutate_the_caller_payload(self):
        payload = legacy_event_payload()
        before = json.dumps(payload, sort_keys=True)
        record_from_json(payload)
        assert json.dumps(payload, sort_keys=True) == before

    def test_legacy_line_in_a_store_is_not_a_corrupt_line(self, tmp_path):
        store = AppendStore(tmp_path / "store")
        store.append(make_spec())
        append_raw_line(store, legacy_event_payload())
        result = store.load()
        assert result.corrupt == []
        assert [type(r) for r in result.records] == [GuardSpec, GuardEvent]
        assert result.records[1].session_id_format is SessionIdFormat.UNCHECKED

    def test_guard_event_missing_any_other_key_is_still_corrupt(self):
        for key in ("drift", "reason", "session_id", "capture_status"):
            payload = make_event(make_spec()).to_json()
            del payload[key]
            with pytest.raises(SchemaError):
                record_from_json(payload)

    def test_guard_event_missing_the_field_and_another_key_is_still_corrupt(self):
        payload = legacy_event_payload()
        del payload["drift"]
        with pytest.raises(SchemaError):
            record_from_json(payload)

    def test_guard_event_missing_the_field_with_an_extra_key_is_still_corrupt(self):
        payload = legacy_event_payload()
        payload["prompt"] = "전문 저장 시도"
        with pytest.raises(SchemaError):
            record_from_json(payload)

    def test_other_record_types_missing_a_key_are_still_corrupt(self):
        for record in all_records():
            if isinstance(record, GuardEvent):
                continue
            payload = record.to_json()
            key = next(k for k in payload if k not in ("record_type", "schema_version"))
            del payload[key]
            with pytest.raises(SchemaError):
                record_from_json(payload)

    def test_other_broken_lines_in_a_store_are_still_corrupt_lines(self, tmp_path):
        store = AppendStore(tmp_path / "store")
        store.append(make_spec())
        broken_event = legacy_event_payload()
        del broken_event["drift"]
        append_raw_line(store, broken_event)
        broken_verdict = make_verdict(make_event(make_spec())).to_json()
        del broken_verdict["reason"]
        append_raw_line(store, broken_verdict)
        result = store.load()
        assert [c.line_no for c in result.corrupt] == [2, 3]
        assert [type(r) for r in result.records] == [GuardSpec]


class TestActionSummary:
    def test_structured_fields_only(self):
        field_names = {f.name for f in dataclasses.fields(ActionSummary)}
        assert field_names == {"tool_name", "command_verb", "target_path", "heredoc"}

    def test_command_verb_is_single_token(self):
        with pytest.raises(SchemaError):
            make_action(command_verb="git push --force origin main")

    def test_no_newlines_in_summary_fields(self):
        with pytest.raises(SchemaError):
            make_action(tool_name="Bash\ncat /etc/passwd")
        with pytest.raises(SchemaError):
            make_action(target_path="a\nb")

    def test_tool_name_required(self):
        with pytest.raises(SchemaError):
            make_action(tool_name="")


class TestGuardSpec:
    def test_content_hash_must_match_semantic_fields(self):
        import dataclasses as dc

        spec = make_spec()
        with pytest.raises(SchemaError):
            dc.replace(spec, content_hash="sha256:" + "f" * 64)

    def test_version_must_be_positive_int(self):
        with pytest.raises(SchemaError):
            make_spec(version=0)

    def test_enforcement_ref_is_optional_metadata(self):
        from rejectbench import EnforcementRef

        spec = make_spec(
            enforcement_ref=EnforcementRef(
                script_path=".claude/hooks/protect-live-reports.sh",
                file_hash="sha256:" + "b" * 64,
            )
        )
        assert spec.enforcement_ref.script_path.endswith(".sh")


class TestGuardDecision:
    def test_modify_requires_resulting_guard_version(self):
        with pytest.raises(SchemaError):
            make_decision(decision=Decision.MODIFY)
        ok = make_decision(decision=Decision.MODIFY, resulting_guard_version=2)
        assert ok.resulting_guard_version == 2

    def test_keep_and_remove_forbid_resulting_version(self):
        with pytest.raises(SchemaError):
            make_decision(decision=Decision.KEEP, resulting_guard_version=2)
        with pytest.raises(SchemaError):
            make_decision(decision=Decision.REMOVE, resulting_guard_version=2)

    def test_duplicate_evidence_ids_rejected(self):
        with pytest.raises(SchemaError):
            make_decision(evidence_event_ids=("ev-1", "ev-1"))


class TestLossAndAmendment:
    def test_loss_kinds(self):
        assert {k.value for k in LossKind} == {
            "write_failure",
            "partial_capture",
            "verdict_failure",
        }

    def test_loss_detail_is_minimal_metadata(self):
        # 원문 없는 최소 메타데이터 — 장문 적재를 스키마가 거부한다.
        with pytest.raises(SchemaError):
            make_loss(detail="x" * 501)

    def test_amendment_requires_reason(self):
        with pytest.raises(SchemaError):
            make_amendment(reason="")

    def test_amendment_carries_previous_value_hash_not_previous_value(self):
        field_names = {f.name for f in dataclasses.fields(Amendment)}
        assert "previous_value_hash" in field_names
        assert "previous_value" not in field_names


class TestSerialization:
    def test_json_roundtrip_preserves_every_record(self):
        for record in all_records():
            payload = record.to_json()
            assert payload["record_type"]
            assert record_from_json(payload) == record

    def test_unknown_record_type_rejected(self):
        with pytest.raises(SchemaError):
            record_from_json({"record_type": "mystery"})

    def test_missing_required_key_rejected(self):
        payload = make_spec().to_json()
        del payload["policy"]
        with pytest.raises(SchemaError):
            record_from_json(payload)

    def test_unexpected_key_rejected(self):
        payload = make_spec().to_json()
        payload["prompt"] = "전문 저장 시도"
        with pytest.raises(SchemaError):
            record_from_json(payload)
