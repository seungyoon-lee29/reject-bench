"""가드 등록부 (T2, spec §3.1·§7).

의미 변경 → 새 버전 강제, 같은 내용 재등록 → 무해 멱등, 기존 버전
덮어쓰기 차단. 조회는 guard_id+version → spec, 최신 버전, 그리고
존재하지 않거나 사건보다 늦은 spec 참조 차단까지 담당한다.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from rejectbench import AppendStore, Dataset, GuardSpec, content_hash, to_json
from rejectbench.registry import (
    EnforcementScriptError,
    GuardRegistry,
    QualityError,
    SpecReferenceError,
    UnknownSpecError,
    VersionConflictError,
    enforcement_ref_for,
)
from tests.factories import make_event, make_spec, ts

DRAFT = dict(
    guard_id="guard-t",
    project="reject-bench",
    purpose="시험 가드",
    policy="위험 명령을 차단한다",
    exceptions=(),
    allow_examples=("git status",),
    block_examples=("git push --force",),
)


def draft(**overrides) -> dict:
    fields = dict(DRAFT)
    fields.update(overrides)
    return fields


@pytest.fixture()
def store(tmp_path) -> AppendStore:
    return AppendStore(tmp_path / "registry")


# --- 등록: 버전 강제 ---------------------------------------------------------


class TestRegister:
    def test_new_guard_becomes_version_1(self, store):
        registry = GuardRegistry(store)
        result = registry.register(**draft(), created_at=ts(0))
        assert result.created is True
        assert result.spec.version == 1
        assert result.spec.guard_id == "guard-t"
        assert result.spec.content_hash == content_hash(
            purpose=DRAFT["purpose"],
            policy=DRAFT["policy"],
            exceptions=(),
            allow_examples=DRAFT["allow_examples"],
            block_examples=DRAFT["block_examples"],
        )

    def test_registration_persists_to_store(self, store):
        GuardRegistry(store).register(**draft(), created_at=ts(0))
        reloaded = GuardRegistry(store)
        assert reloaded.get("guard-t", 1).policy == DRAFT["policy"]

    def test_same_content_is_idempotent_no_new_record(self, store):
        registry = GuardRegistry(store)
        first = registry.register(**draft(), created_at=ts(0))
        second = registry.register(**draft(), created_at=ts(5))
        assert second.created is False
        assert second.spec == first.spec  # 기존 레코드 그대로, 시각도 원본 유지
        specs = [r for r in store.load().records if isinstance(r, GuardSpec)]
        assert len(specs) == 1

    def test_changed_content_forces_next_version(self, store):
        registry = GuardRegistry(store)
        v1 = registry.register(**draft(), created_at=ts(0)).spec
        v2 = registry.register(
            **draft(policy="위험 명령과 heredoc을 차단한다"), created_at=ts(5)
        ).spec
        assert v2.version == 2
        assert v2.content_hash != v1.content_hash
        # v1은 덮어써지지 않고 그대로 남는다.
        assert registry.get("guard-t", 1) == v1

    def test_revert_content_is_still_a_new_version(self, store):
        registry = GuardRegistry(store)
        v1 = registry.register(**draft(), created_at=ts(0)).spec
        registry.register(**draft(policy="다른 정책"), created_at=ts(5))
        v3 = registry.register(**draft(), created_at=ts(10)).spec
        # 최신(v2)과 내용이 다르므로 새 버전이다 — 과거 버전 재사용이 아니다.
        assert v3.version == 3
        assert v3.content_hash == v1.content_hash
        assert registry.latest("guard-t") == v3

    def test_guards_version_independently(self, store):
        registry = GuardRegistry(store)
        registry.register(**draft(), created_at=ts(0))
        other = registry.register(**draft(guard_id="guard-u"), created_at=ts(5)).spec
        assert other.version == 1


# --- 등록: 최소 품질 검증 ----------------------------------------------------


class TestQuality:
    @pytest.mark.parametrize("policy", ["", "   "])
    def test_blank_policy_rejected(self, store, policy):
        with pytest.raises(QualityError):
            GuardRegistry(store).register(**draft(policy=policy), created_at=ts(0))

    def test_empty_allow_examples_rejected(self, store):
        with pytest.raises(QualityError):
            GuardRegistry(store).register(**draft(allow_examples=()), created_at=ts(0))

    def test_empty_block_examples_rejected(self, store):
        with pytest.raises(QualityError):
            GuardRegistry(store).register(**draft(block_examples=()), created_at=ts(0))

    def test_blank_list_entries_rejected(self, store):
        with pytest.raises(QualityError):
            GuardRegistry(store).register(**draft(exceptions=(" ",)), created_at=ts(0))

    def test_rejected_draft_appends_nothing(self, store):
        registry = GuardRegistry(store)
        with pytest.raises(QualityError):
            registry.register(**draft(policy=""), created_at=ts(0))
        assert not store.path.exists() or store.load().records == []


# --- enforcement_ref ---------------------------------------------------------


class TestEnforcementRef:
    def test_hashes_script_bytes(self, tmp_path):
        script = tmp_path / "guard.sh"
        script.write_bytes(b"#!/bin/sh\nexit 2\n")
        ref = enforcement_ref_for(script)
        expected = hashlib.sha256(b"#!/bin/sh\nexit 2\n").hexdigest()
        assert ref.file_hash == f"sha256:{expected}"
        assert ref.script_path == str(script)

    def test_missing_script_is_a_clear_error(self, tmp_path):
        missing = tmp_path / "no-such-guard.sh"
        with pytest.raises(EnforcementScriptError, match="no-such-guard.sh"):
            enforcement_ref_for(missing)

    def test_registered_spec_carries_ref(self, store, tmp_path):
        script = tmp_path / "guard.sh"
        script.write_bytes(b"exit 2\n")
        ref = enforcement_ref_for(script)
        spec = GuardRegistry(store).register(
            **draft(), enforcement_ref=ref, created_at=ts(0)
        ).spec
        assert spec.enforcement_ref == ref
        assert GuardRegistry(store).get("guard-t", 1).enforcement_ref == ref


# --- 조회와 참조 차단 --------------------------------------------------------


class TestLookup:
    def test_get_unknown_guard(self, store):
        with pytest.raises(UnknownSpecError):
            GuardRegistry(store).get("ghost", 1)

    def test_get_unknown_version(self, store):
        registry = GuardRegistry(store)
        registry.register(**draft(), created_at=ts(0))
        with pytest.raises(UnknownSpecError):
            registry.get("guard-t", 2)

    def test_latest_unknown_guard(self, store):
        with pytest.raises(UnknownSpecError):
            GuardRegistry(store).latest("ghost")

    def test_versions_in_order(self, store):
        registry = GuardRegistry(store)
        registry.register(**draft(), created_at=ts(0))
        registry.register(**draft(policy="개정 정책"), created_at=ts(5))
        assert [s.version for s in registry.versions("guard-t")] == [1, 2]
        assert registry.guard_ids() == ["guard-t"]

    def test_resolve_reference_returns_prior_spec(self, store):
        registry = GuardRegistry(store)
        spec = registry.register(**draft(), created_at=ts(0)).spec
        assert registry.resolve_reference("guard-t", 1, occurred_at=ts(10)) == spec

    def test_resolve_reference_blocks_missing_spec(self, store):
        with pytest.raises(SpecReferenceError):
            GuardRegistry(store).resolve_reference("ghost", 1, occurred_at=ts(10))

    def test_resolve_reference_blocks_spec_later_than_event(self, store):
        registry = GuardRegistry(store)
        registry.register(**draft(), created_at=ts(10))
        with pytest.raises(SpecReferenceError):
            registry.resolve_reference("guard-t", 1, occurred_at=ts(0))


# --- 덮어쓰기 차단 (저장소 수준) ---------------------------------------------


class TestOverwriteBlocked:
    def test_conflicting_duplicate_version_refused_on_load(self, store):
        store.append(make_spec(guard_id="guard-t", version=1))
        store.append(
            make_spec(
                guard_id="guard-t",
                version=1,
                spec_id="spec-guard-t-v1b",
                policy="몰래 바뀐 정책",
            )
        )
        with pytest.raises(VersionConflictError):
            GuardRegistry(store)

    def test_identical_duplicate_line_tolerated(self, store):
        spec = make_spec(guard_id="guard-t", version=1)
        store.append(spec)
        store.append(spec)
        assert GuardRegistry(store).get("guard-t", 1) == spec


# --- 두 버전 시나리오: 과거 사건은 항상 과거 해시를 참조한다 -----------------


class TestTwoVersionScenario:
    def test_past_event_keeps_past_hash(self, store):
        registry = GuardRegistry(store)
        v1 = registry.register(**draft(), created_at=ts(0)).spec
        event = make_event(spec=v1, occurred_at=ts(10))
        v2 = registry.register(
            **draft(policy="개정: heredoc도 차단한다"), created_at=ts(20)
        ).spec

        # 사건의 참조는 등록부에서 언제나 과거 버전·과거 해시로 해석된다.
        resolved = registry.resolve_reference(
            event.guard_id, event.guard_version, occurred_at=event.occurred_at
        )
        assert resolved == v1
        assert event.guard_spec_hash == v1.content_hash
        assert event.guard_spec_hash != v2.content_hash
        assert registry.latest("guard-t") == v2

        # T1 dataset 무결성 검사와도 이어진다: append 순서 그대로 위반 없음.
        specs = [r for r in store.load().records if isinstance(r, GuardSpec)]
        dataset = Dataset([specs[0], event, specs[1]])
        assert dataset.check_integrity() == []

    def test_json_round_trip_preserves_reference(self, store):
        registry = GuardRegistry(store)
        v1 = registry.register(**draft(), created_at=ts(0)).spec
        registry.register(**draft(policy="개정 정책"), created_at=ts(5))
        payload = json.loads(json.dumps(to_json(v1)))
        assert payload["content_hash"] == GuardRegistry(store).get("guard-t", 1).content_hash
