"""가드 발동 기록기 (spec §3.2 규칙, §3.6, §5 "발동 시" 1~5).

최우선 계약: **기록은 가드 발동을 블로킹하지 않는다.** 공개 진입점
`record_guard_result`는 어떤 예외도 밖으로 던지지 않는다. 주 저장이
실패하면 LossRecord를 시도하고, 그것마저 실패하면 대체 매체(OS 임시 경로
파일, 마지막으로 stderr)에 최소 흔적을 남긴다. 두 매체 모두 실패할
가능성은 남으며 이를 절대 보장이라고 주장하지 않는다.

페이로드 해석은 Claude Code PreToolUse 훅 스키마(`session_id`, `cwd`,
`tool_name`, `tool_input`)만 구현한다. 다른 실행기의 페이로드는 해석하지
않은 채 unknown/no_context + partial로 기록한다 — 실행기 중립 입구는
`harness` 인자이며 Codex 전용 파싱은 넣지 않는다.

저장하는 것은 구조화 행동 요약뿐이다: 전체 명령·파일 내용을 담을 자리가
스키마(`ActionSummary`)에 없고, 차단 사유는 적재 시점 비밀 제거를 거친다.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from rejectbench.origin import decide_origin
from rejectbench.records import (
    ActionSummary,
    CaptureStatus,
    GuardEvent,
    GuardSpec,
    LossKind,
    LossRecord,
)
from rejectbench.registry import GuardRegistry, enforcement_ref_for
from rejectbench.scrub import scrub_text
from rejectbench.store import AppendStore, production_root

# 세션 test 플래그 — 이름 고정. "값 존재"가 기준이므로 빈 문자열도 켠 것이다.
TEST_SESSION_ENV = "REJECTBENCH_TEST_SESSION"
# 저장소 루트 override. 없으면 운영 중앙 경로(data/v7).
STORE_ENV = "REJECTBENCH_STORE"
# 대체 매체 디렉터리 override. 없으면 OS 임시 경로.
FALLBACK_DIR_ENV = "REJECTBENCH_FALLBACK_DIR"
FALLBACK_FILENAME = "rejectbench-loss-fallback.log"

_MAX_REASON = 4000


@dataclass(frozen=True)
class GuardResult:
    """가드 프로세스의 관찰 결과 — 해석 전의 원자료."""

    exit_code: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class BlockJudgement:
    blocked: bool
    reason: str  # 비밀 제거 전 원문 — 저장 직전에만 scrub한다


@dataclass(frozen=True)
class RecordOutcome:
    """기록 시도의 관찰 가능한 결과. 가드 결과에는 영향이 없다."""

    blocked: bool
    recorded: bool
    event: GuardEvent | None = None
    loss_recorded: bool = False
    fallback_used: bool = False


# --- 차단 판정 (관측 대상 2종의 인터페이스) ----------------------------------


def interpret_guard_result(result: GuardResult) -> BlockJudgement:
    """차단 여부 판정: exit 2(stderr 사유) 또는 permissionDecision=deny JSON."""
    deny_reason = _deny_reason(result.stdout)
    if result.exit_code == 2:
        reason = result.stderr.strip() or (deny_reason or "")
        return BlockJudgement(blocked=True, reason=reason)
    if deny_reason is not None:
        return BlockJudgement(blocked=True, reason=deny_reason)
    return BlockJudgement(blocked=False, reason="")


def _deny_reason(stdout: str) -> str | None:
    text = stdout.strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    nested = payload.get("hookSpecificOutput")
    for candidate in (payload, nested if isinstance(nested, dict) else None):
        if candidate and candidate.get("permissionDecision") == "deny":
            reason = candidate.get("permissionDecisionReason")
            return reason if isinstance(reason, str) else ""
    return None


# --- 페이로드 해석 (Claude Code 경로만) --------------------------------------


def parse_claude_payload(payload_text: str) -> dict | None:
    try:
        payload = json.loads(payload_text)
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def extract_action(payload: dict | None) -> ActionSummary:
    """구조화 행동 요약 — 도구 이름·명령 동사(첫 토큰)·대상 경로·heredoc 여부.

    전체 명령·파일 내용은 스키마상 담을 자리가 없다 (spec §4).
    """
    if not isinstance(payload, dict):
        return ActionSummary(tool_name="unknown")
    tool_name = payload.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name:
        tool_name = "unknown"
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}

    verb: str | None = None
    target: str | None = None
    heredoc = False
    command = tool_input.get("command")
    if isinstance(command, str) and command.split():
        heredoc = "<<" in command
        tokens = command.split()
        verb = tokens[0]
        target = _first_pathlike(tokens[1:])
    else:
        for key in ("file_path", "notebook_path", "path"):
            value = tool_input.get(key)
            if isinstance(value, str) and value:
                target = value.splitlines()[0]
                break
    return ActionSummary(
        tool_name=tool_name,
        command_verb=scrub_text(verb) if verb else None,
        target_path=scrub_text(target) if target else None,
        heredoc=heredoc,
    )


def _first_pathlike(tokens: list[str]) -> str | None:
    for token in tokens:
        candidate = token.strip("'\"`;,")
        if not candidate or candidate.startswith("-"):
            continue
        if "/" in candidate or candidate.startswith("~"):
            return candidate
    return None


# --- 등록부 연계 -------------------------------------------------------------


def _normalized(path: str) -> str:
    return os.path.realpath(os.path.expanduser(path))


def _match_spec(store, guard_path: str) -> GuardSpec | None:
    """enforcement_ref 경로로 등록된 spec을 찾는다. 못 찾거나 등록부가 깨졌으면 None."""
    try:
        registry = GuardRegistry(store)
        target = _normalized(guard_path)
        for guard_id in registry.guard_ids():
            for spec in reversed(registry.versions(guard_id)):
                ref = spec.enforcement_ref
                if ref is not None and _normalized(ref.script_path) == target:
                    return spec
    except Exception:
        return None
    return None


def _detect_drift(spec: GuardSpec, guard_path: str) -> bool:
    """기록 시점 가드 파일 SHA-256 ≠ spec enforcement_ref → drift. 읽기만 한다."""
    ref = spec.enforcement_ref
    if ref is None:
        return False
    try:
        current = enforcement_ref_for(guard_path)
    except Exception:
        return False  # 대조 불가 — drift라고 단정하지 않는다
    return current.file_hash != ref.file_hash


# --- 조립 --------------------------------------------------------------------


def resolve_store_root(env: Mapping[str, str]) -> Path:
    override = env.get(STORE_ENV)
    return Path(override) if override else production_root()


def assemble_event(
    *,
    payload: dict | None,
    guard_path: str,
    reason: str,
    harness: str,
    env: Mapping[str, str],
    store,
    now: datetime,
) -> GuardEvent:
    session_raw = payload.get("session_id") if payload else None
    context_available = isinstance(session_raw, str) and bool(session_raw)
    origin, evidence = decide_origin(
        context_available=context_available, test_flag=TEST_SESSION_ENV in env
    )
    capture = CaptureStatus.COMPLETE if context_available else CaptureStatus.PARTIAL
    session_id = f"{harness}:{session_raw}" if context_available else f"{harness}:unknown"
    cwd = payload.get("cwd") if payload else None
    project = Path(cwd).name if isinstance(cwd, str) and cwd else "unknown"

    spec = _match_spec(store, guard_path)
    if spec is None:
        guard_fields: dict = {"unregistered": True, "guard_hint": guard_path}
        drift = False
    else:
        guard_fields = {
            "guard_id": spec.guard_id,
            "guard_version": spec.version,
            "guard_spec_hash": spec.content_hash,
        }
        drift = _detect_drift(spec, guard_path)

    scrubbed = scrub_text(reason)
    if len(scrubbed) > _MAX_REASON:
        scrubbed = scrubbed[:_MAX_REASON] + "…[truncated]"
    return GuardEvent(
        event_id=f"ev-{uuid.uuid4().hex}",
        occurred_at=now,
        session_id=session_id,
        project=project,
        action=extract_action(payload),
        reason=scrubbed,
        origin=origin,
        origin_evidence=evidence,
        capture_status=capture,
        drift=drift,
        **guard_fields,
    )


# --- 기록 (비블로킹) ---------------------------------------------------------


def record_guard_result(
    *,
    payload_text: str,
    guard_path: str,
    result: GuardResult,
    harness: str = "claude",
    env: Mapping[str, str] | None = None,
    store=None,
    now: datetime | None = None,
) -> RecordOutcome:
    """차단으로 판정되면 GuardEvent를 기록한다. 절대 예외를 던지지 않는다."""
    try:
        env = os.environ if env is None else env
        judgement = interpret_guard_result(result)
        if not judgement.blocked:
            return RecordOutcome(blocked=False, recorded=False)
        if store is None:
            store = AppendStore(resolve_store_root(env))
        now = now if now is not None else datetime.now(timezone.utc)
        payload = parse_claude_payload(payload_text) if harness == "claude" else None
        event: GuardEvent | None = None
        try:
            event = assemble_event(
                payload=payload,
                guard_path=guard_path,
                reason=judgement.reason,
                harness=harness,
                env=env,
                store=store,
                now=now,
            )
            store.append(event)
            return RecordOutcome(blocked=True, recorded=True, event=event)
        except Exception as exc:
            return _record_loss(store=store, env=env, now=now, exc=exc, event=event)
    except Exception:
        # 마지막 방어선 — 기록기 자체 결함도 가드 결과를 바꾸지 않는다.
        try:
            fallback_used = _write_fallback_trace(
                env=env if isinstance(env, Mapping) else {},
                trace={"kind": LossKind.WRITE_FAILURE.value, "detail": "recorder 내부 오류"},
            )
        except Exception:
            fallback_used = False
        return RecordOutcome(blocked=False, recorded=False, fallback_used=fallback_used)


def _record_loss(
    *, store, env: Mapping[str, str], now: datetime, exc: Exception, event: GuardEvent | None
) -> RecordOutcome:
    # 원문 없는 최소 메타데이터 — 예외 메시지는 페이로드 조각을 담을 수 있어
    # 클래스 이름만 남긴다 (spec §3.6).
    loss = LossRecord(
        loss_id=f"loss-{uuid.uuid4().hex}",
        recorded_at=now,
        kind=LossKind.WRITE_FAILURE,
        detail=f"guard_event 기록 실패: {type(exc).__name__}"[:500],
        subject_ref=event.event_id if event is not None else None,
    )
    try:
        store.append(loss)
        return RecordOutcome(blocked=True, recorded=False, event=event, loss_recorded=True)
    except Exception:
        fallback_used = _write_fallback_trace(
            env=env,
            trace={
                "loss_id": loss.loss_id,
                "recorded_at": loss.recorded_at.isoformat(),
                "kind": loss.kind.value,
                "detail": loss.detail,
                "subject_ref": loss.subject_ref,
            },
        )
        return RecordOutcome(
            blocked=True, recorded=False, event=event, fallback_used=fallback_used
        )


def _write_fallback_trace(*, env: Mapping[str, str], trace: dict) -> bool:
    """대체 매체 최소 흔적: OS 임시 경로 파일, 실패 시 stderr. 그마저 실패할 수 있다."""
    line = json.dumps(trace, ensure_ascii=False, sort_keys=True)
    try:
        directory = Path(env.get(FALLBACK_DIR_ENV) or tempfile.gettempdir())
        with open(directory / FALLBACK_FILENAME, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        return True
    except Exception:
        try:
            print(f"rejectbench loss: {line}", file=sys.stderr)
            return True
        except Exception:
            return False
