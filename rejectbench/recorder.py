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
import pwd
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
    SessionIdFormat,
    session_id_format,
    split_session_id,
)
from rejectbench.registry import GuardRegistry, enforcement_ref_for
from rejectbench.scrub import redact_command_echo, scrub_text
from rejectbench.store import AppendStore, production_root

# 세션 test 플래그 — 이름 고정. "값 존재"가 기준이므로 빈 문자열도 켠 것이다.
TEST_SESSION_ENV = "REJECTBENCH_TEST_SESSION"
# 저장소 루트 override. 없으면 운영 중앙 경로(data/v7).
STORE_ENV = "REJECTBENCH_STORE"
# 대체 매체 디렉터리 override. 없으면 OS 임시 경로.
FALLBACK_DIR_ENV = "REJECTBENCH_FALLBACK_DIR"
FALLBACK_FILENAME = "rejectbench-loss-fallback.log"

# 차단 사유 본문 상한 (마커 별도).
_MAX_REASON = 4000
# 공백 되물림을 포기하는 하한. **절대 문자 수가 아니라 상한에 대한 비율**이다 —
# 상한을 튜닝하면 하한이 함께 움직여야 하고, 두 상수가 따로 놀면 조용히 깨진다.
_MIN_REASON_RATIO = 0.5
_MIN_REASON = int(_MAX_REASON * _MIN_REASON_RATIO)
_TRUNCATION_MARKER = "…[truncated]"


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


# --- 차단 사유 절단 (spec §3) -------------------------------------------------
#
# 절단점은 ① 공백 되물림 → ② 민감값 가로지름 회피(고정점, 항상) → ③ 하한 폴백
# 순으로 정한다. ①은 정돈이고 ②는 안전 보장이라 폴백 경로에도 ②는 남는다.
# 보장 범위는 **이 사건의 민감값 세 종**에 한한 새 파편 생성 차단이다 — 사유에
# 섞인 타 세션 ID는 대상이 아니다(알려면 store를 읽어야 하고, 그 I/O는 no-throw
# 봉투 안에서 감당하지 않는다).


def _home_path(env: Mapping[str, str]) -> str:
    """절단이 아는 홈 절대 경로 원문. 알 수 없거나 루트면 대상에서 뺀다.

    주입된 `env`만 본다 — `Path.home()`은 프로세스 환경을 읽어 "홈 없음" 경로가
    개발자 실제 홈으로 도는 것을 숨긴다. env에 없으면 계정 DB(pwd)로 폴백한다.
    끝 슬래시는 정규화한다: `HOME=/Users/x/`이면 사유 속 `/Users/x`가 needle과
    어긋나 보호가 빠지고, 조회 경계(`Path.home()` 기반)와도 다른 값을 보게 된다.
    """
    home = env.get("HOME") or ""
    if not home:
        try:
            home = pwd.getpwuid(os.getuid()).pw_dir
        except (KeyError, OSError, AttributeError):  # 홈을 알 수 없는 환경
            home = ""
    home = home.rstrip("/")
    # 루트("/")는 rstrip 뒤 빈 문자열이다 — 루트를 홈으로 잡으면 모든 경로 조각이
    # 민감값이 되므로 그대로 대상에서 뺀다.
    return home


def _sensitive_values(
    *, env: Mapping[str, str], session_id: str, context_available: bool
) -> tuple[str, ...]:
    """적재 시점에 아는 민감값 — 홈 절대 경로·복합 세션 ID·원본 세션 ID.

    원본은 페이로드가 아니라 저장 복합값을 `split_session_id`로 되분해해 얻는다
    — needle 산출이 §4 형식 검사·§5 조회 경계와 한 함수를 쓰게 하기 위해서다.
    자리표시(`harness:unknown`)의 원본 부분은 민감값이 아니다: 일반 단어
    `unknown`을 needle로 삼으면 사유가 부당하게 줄어든다.
    """
    _, raw = split_session_id(session_id)
    candidates = (_home_path(env), session_id, raw if context_available and raw else "")
    return tuple(value for value in candidates if value)


def _whitespace_cut(text: str, cut: int) -> int:
    """공백 구분 단위를 반토막 내지 않는 절단점 — `cut` 이하.

    경계 술어는 `str.isspace()`다 — 계약의 "공백 구분 단위"가 `str.split()`
    경계이고 `split()`이 쓰는 술어가 이것이다. `cut` 자리가 공백류면 단위가
    거기서 정확히 끝난 것이라 되물릴 이유가 없다. 그 외에는 앞 `cut`자 안의
    마지막 공백류 앞까지 되물린다. 뒤에서 훑는 선형 구현을 쓴다 — 정규식 판은
    공백 없는 한 덩어리 입력에서 역추적으로 2차 시간이 된다(plan.md E1).
    """
    if cut < len(text) and text[cut].isspace():
        return cut
    head = text[:cut]
    index = len(head)
    while index > 0 and not head[index - 1].isspace():
        index -= 1  # 절단점이 자른 비공백 연속열을 통째로 버린다
    while index > 0 and head[index - 1].isspace():
        index -= 1  # 그 앞 공백류도 본문에 남기지 않는다
    return index


def _retreat_past_sensitive(text: str, cut: int, sensitive: tuple[str, ...]) -> int:
    """절단점이 민감값 등장을 가로지르면 그 등장의 시작 전까지 되물린다 — 고정점.

    가로지름 = 등장이 `cut` 앞에서 시작해 `cut` 뒤에서 끝난다. **가로지를
    때만** 되물린다 — 본문 안에 온전히 든 등장까지 되물리면 사유가 통째로
    사라진다. 찾는 창의 상한이 `cut - 1`이라 찾은 시작은 항상 `cut`보다 앞이고,
    따라서 루프는 유한 단계에 멈춘다.
    """
    while cut > 0:
        starts = []
        for value in sensitive:
            span = len(value)
            # 창 [cut-span+1, cut-1]: 이 안에서 시작하는 등장만 cut을 가로지른다
            found = text.find(value, max(0, cut - span + 1), cut + span - 1)
            if found != -1:
                starts.append(found)
        if not starts:
            break
        cut = min(starts)
    return cut


def _cut_point(text: str, sensitive: tuple[str, ...]) -> int:
    """본문 절단점 — ① 공백 되물림 → ② 민감값 고정점 → ③ 하한 폴백."""
    tidy = _retreat_past_sensitive(text, _whitespace_cut(text, _MAX_REASON), sensitive)
    if tidy >= _MIN_REASON:
        return tidy
    # 정돈이 본문 절반 이상을 먹으면 정돈을 버린다. 안전 보장(②)은 폴백에도 남는다.
    return _retreat_past_sensitive(text, _MAX_REASON, sensitive)


def _truncate_reason(
    text: str, *, env: Mapping[str, str], session_id: str, context_available: bool
) -> str:
    """상한 초과 사유만 절단한다. 절단점 계산이 죽으면 맹목 절단(spec §3.5).

    이 폴백은 `assemble_event` 안에서 닫혀야 한다 — 예외가 밖으로 새면
    `record_guard_result`가 `_record_loss`로 보내 `recorded=False`가 된다.
    """
    if len(text) <= _MAX_REASON:
        return text
    try:
        cut = _cut_point(
            text,
            _sensitive_values(
                env=env, session_id=session_id, context_available=context_available
            ),
        )
    except Exception:
        cut = _MAX_REASON  # 절단 계산 결함이 사건을 LossRecord로 강등시키지 않는다
    return text[:cut] + _TRUNCATION_MARKER


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

    scrubbed = _truncate_reason(
        scrub_text(redact_command_echo(reason)),
        env=env,
        session_id=session_id,
        context_available=context_available,
    )
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
        session_id_format=_session_id_format(session_id, context_available),
        **guard_fields,
    )


def _session_id_format(session_id: str, context_available: bool) -> SessionIdFormat:
    """§4 진단값 — 자리표시는 미검사, 술어 예외도 미검사(사건 보존 우선, spec §4.6).

    이 폴백도 assemble_event 안에서 닫힌다 — 검사·표시가 기록 실패로 새면 안 된다.
    """
    if not context_available:
        return SessionIdFormat.UNCHECKED
    try:
        return session_id_format(session_id)
    except Exception:
        return SessionIdFormat.UNCHECKED


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
