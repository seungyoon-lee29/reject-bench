"""테스트용 레코드 빌더. 의미 있는 필드만 각 테스트가 덮어쓴다."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from rejectbench import (
    ActionSummary,
    CaptureStatus,
    Decision,
    GuardDecision,
    GuardEvent,
    GuardSpec,
    Origin,
    OriginEvidence,
    PolicyVerdict,
    Utility,
    UtilityReview,
    Verdict,
    content_hash,
)

T0 = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)


def ts(minutes: int = 0) -> datetime:
    return T0 + timedelta(minutes=minutes)


def make_spec(
    guard_id: str = "guard-a",
    version: int = 1,
    spec_id: str | None = None,
    project: str = "reject-bench",
    purpose: str = "위험 git 명령이 이력을 파괴하는 사고 방지",
    policy: str = "force push와 hard reset을 차단한다",
    exceptions: tuple[str, ...] = (),
    allow_examples: tuple[str, ...] = ("git status",),
    block_examples: tuple[str, ...] = ("git push --force",),
    created_at: datetime | None = None,
    **kwargs,
) -> GuardSpec:
    return GuardSpec(
        spec_id=spec_id or f"spec-{guard_id}-v{version}",
        guard_id=guard_id,
        version=version,
        project=project,
        purpose=purpose,
        policy=policy,
        exceptions=tuple(exceptions),
        allow_examples=tuple(allow_examples),
        block_examples=tuple(block_examples),
        created_at=created_at or ts(0),
        content_hash=content_hash(
            purpose=purpose,
            policy=policy,
            exceptions=tuple(exceptions),
            allow_examples=tuple(allow_examples),
            block_examples=tuple(block_examples),
        ),
        **kwargs,
    )


def make_action(**overrides) -> ActionSummary:
    fields = {
        "tool_name": "Bash",
        "command_verb": "git",
        "target_path": "reports/live.md",
        "heredoc": False,
    }
    fields.update(overrides)
    return ActionSummary(**fields)


def make_event(
    spec: GuardSpec | None = None,
    event_id: str = "ev-1",
    session_id: str = "claude:s-1",
    project: str = "reject-bench",
    occurred_at: datetime | None = None,
    origin: Origin = Origin.OPERATION,
    origin_evidence: OriginEvidence = OriginEvidence.DEFAULT_INHERITED,
    capture_status: CaptureStatus = CaptureStatus.COMPLETE,
    **kwargs,
) -> GuardEvent:
    guard_fields: dict = {}
    if spec is not None:
        guard_fields = {
            "guard_id": spec.guard_id,
            "guard_version": spec.version,
            "guard_spec_hash": spec.content_hash,
        }
    guard_fields.update(kwargs)
    return GuardEvent(
        event_id=event_id,
        occurred_at=occurred_at or ts(10),
        session_id=session_id,
        project=project,
        action=guard_fields.pop("action", make_action()),
        reason=guard_fields.pop("reason", "blocked: force push matched policy"),
        origin=origin,
        origin_evidence=origin_evidence,
        capture_status=capture_status,
        **guard_fields,
    )


def make_verdict(
    event: GuardEvent,
    verdict_id: str = "vd-1",
    verdict: Verdict = Verdict.CORRECT_BLOCK,
    judged_at: datetime | None = None,
    **kwargs,
) -> PolicyVerdict:
    return PolicyVerdict(
        verdict_id=verdict_id,
        event_id=event.event_id,
        verdict=verdict,
        reason=kwargs.pop("reason", "정책 조항과 일치, 명시 예외 비해당"),
        context_bundle_hash=kwargs.pop("context_bundle_hash", "sha256:" + "0" * 64),
        guard_spec_hash=kwargs.pop("guard_spec_hash", event.guard_spec_hash or "sha256:" + "1" * 64),
        rubric_hash=kwargs.pop("rubric_hash", "sha256:" + "2" * 64),
        model_id=kwargs.pop("model_id", "judge-model-1"),
        model_settings_hash=kwargs.pop("model_settings_hash", "sha256:" + "3" * 64),
        judged_at=judged_at or ts(60),
        **kwargs,
    )


def make_review(
    event: GuardEvent,
    review_id: str = "rv-1",
    utility: Utility = Utility.USEFUL,
    reviewed_at: datetime | None = None,
    **kwargs,
) -> UtilityReview:
    return UtilityReview(
        review_id=review_id,
        event_id=event.event_id,
        utility=utility,
        note=kwargs.pop("note", "실제로 사고를 막았다"),
        reviewed_at=reviewed_at or ts(90),
        **kwargs,
    )


def make_decision(
    guard_id: str = "guard-a",
    decision_id: str = "dc-1",
    decision: Decision = Decision.KEEP,
    evidence_event_ids: tuple[str, ...] = (),
    decided_at: datetime | None = None,
    **kwargs,
) -> GuardDecision:
    return GuardDecision(
        decision_id=decision_id,
        guard_id=guard_id,
        decision=decision,
        evidence_event_ids=tuple(evidence_event_ids),
        rationale=kwargs.pop("rationale", "여러 세션 근거로 유지"),
        decided_at=decided_at or ts(120),
        **kwargs,
    )
