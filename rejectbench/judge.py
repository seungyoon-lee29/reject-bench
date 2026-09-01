"""세션 뒤 독립 LLM 정책 판정 실행기 (spec §3.3, §5 "세션 뒤" 1~3, §4).

- 대상: 새 `operation` 사건 중 PolicyVerdict가 없는 전부. `unregistered`·
  `test`·`unknown` 사건은 판정 대상이 아니다 — 건수만 세어 보고 경로로 넘긴다.
- 사건당 독립 LLM 1회 호출. API·파싱 실패는 미처리로 유지한다(재샘플링 금지)
  — LossRecord(`verdict_failure`)만 남기고 verdict를 만들지 않는다.
- 실제 판정 전에 참조 spec의 allow/block 예시로 판정기를 교정 검사하고
  교정 레코드(`judge_calibration`)를 append한다. 교정 미통과·미실시 설정의
  판정에는 그 사실을 reason에 병기한다.
- 재판정은 이전 레코드를 보존하고(append 전용) 사유·새 설정을 명시한다.
- 전송 계층은 주입 가능하다. 운영 기본은 OpenAI Chat Completions를 표준
  라이브러리 urllib로 직접 호출하는 `OpenAITransport`이며, API 키는
  `OPENAI_API_KEY` 환경변수로만 읽고 레코드·로그·예외 메시지에 남기지 않는다.
- 응답은 verdict JSON으로만 파싱한다 — 어떤 내용도 실행하지 않는다 (spec §4).
- 비용 승인 게이트: 과금 호출 승인은 CLI `--approve-billing` 또는 env
  `REJECTBENCH_BILLING_APPROVED=1`로만 한다. 게이트 판단 자료는 `billing_plan`이
  만들고, 게이트 자체는 CLI가 강제한다 (dry-run이 기본).
"""

from __future__ import annotations

import fcntl
import json
import os
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Protocol

from rejectbench.dataset import Dataset
from rejectbench.hashing import value_hash
from rejectbench.records import (
    SCHEMA_VERSION,
    GuardSpec,
    LossKind,
    LossRecord,
    Origin,
    PolicyVerdict,
    SchemaError,
    Verdict,
)
from rejectbench.rubric import (
    build_calibration_bundle,
    build_context_bundle,
    calibration_cases,
    context_bundle_hash,
    render_messages,
    rubric_hash,
)
from rejectbench.scrub import scrub_text
from rejectbench.store import AppendStore

# gpt-5 계열은 `temperature` 고정을 거부한다(기본값 1만 허용) — 결정성을 지키려면
# temperature를 받는 모델을 기본으로 둔다. 최신 모델이 필요하면 `--model`로 바꾸되
# `--model-settings '{}'`로 temperature를 함께 빼야 한다(그 경우 판정은 비결정적).
DEFAULT_MODEL_ID = "gpt-4.1-mini"
# temperature 0 상당의 결정적 설정 — 해시(`model_settings_hash`)로 고정된다.
DEFAULT_MODEL_SETTINGS: dict = {"temperature": 0}

OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
API_KEY_ENV = "OPENAI_API_KEY"
BILLING_ENV = "REJECTBENCH_BILLING_APPROVED"

CALIBRATION_FILENAME = "calibration.jsonl"
CALIBRATION_RECORD_TYPE = "judge_calibration"

NOTE_CALIBRATION_NOT_RUN = "[교정 미실시]"
NOTE_CALIBRATION_FAILED = "[교정 미통과]"

_MAX_REASON = 4000


class JudgeError(Exception):
    """판정 실행기 규칙 위반."""


class TransportError(JudgeError):
    """판정 API 호출 실패 — 해당 사건은 미처리로 남는다."""


class MissingApiKeyError(TransportError):
    """API 키 환경변수 부재. 키 값은 어디에도 담지 않는다."""


class VerdictParseError(JudgeError):
    """응답이 verdict JSON이 아니다 — 해당 사건은 미처리로 남는다."""


# --- 전송 계층 ---------------------------------------------------------------


class JudgeTransport(Protocol):
    """주입 가능한 전송 인터페이스. 테스트는 전부 fake 전송으로 돈다."""

    def complete(self, *, model_id: str, messages: list[dict], settings: dict) -> str: ...


class OpenAITransport:
    """OpenAI Chat Completions 직접 호출 (표준 라이브러리 urllib, SDK 없음).

    예외 메시지에 API 키·요청 본문·응답 본문을 담지 않는다.
    """

    def __init__(
        self,
        *,
        env: Mapping[str, str] | None = None,
        url: str = OPENAI_API_URL,
        timeout: float = 120.0,
        opener: Callable | None = None,
    ):
        env = os.environ if env is None else env
        key = env.get(API_KEY_ENV)
        if not key:
            raise MissingApiKeyError(f"{API_KEY_ENV} 환경변수가 설정돼 있지 않다")
        self._key = key
        self._url = url
        self._timeout = timeout
        self._opener = opener if opener is not None else urllib.request.urlopen

    def complete(self, *, model_id: str, messages: list[dict], settings: dict) -> str:
        body = json.dumps(
            {"model": model_id, "messages": messages, **settings}, ensure_ascii=False
        ).encode("utf-8")
        request = urllib.request.Request(
            self._url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._key}",
            },
            method="POST",
        )
        try:
            with self._opener(request, timeout=self._timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # 본문·헤더를 예외에 싣지 않는다 — 키·프롬프트 유출 방지.
            raise TransportError(f"판정 API HTTP {exc.code}") from None
        except urllib.error.URLError as exc:
            reason = exc.reason
            detail = reason if isinstance(reason, str) else type(reason).__name__
            raise TransportError(f"판정 API 연결 실패: {detail}") from None
        except (OSError, ValueError) as exc:
            raise TransportError(f"판정 API 응답 수신 실패: {type(exc).__name__}") from None
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise TransportError("판정 API 응답 형식이 예상과 다르다") from None
        if not isinstance(content, str):
            raise TransportError("판정 API 응답 content가 문자열이 아니다")
        return content


# --- 응답 파싱 (엄격한 JSON — 실행 금지) --------------------------------------


def parse_verdict_response(text: str) -> tuple[Verdict, str]:
    """응답을 verdict JSON으로만 파싱한다. JSON 밖 텍스트는 무시하고,
    파싱 실패는 미처리다. 응답의 어떤 내용도 실행하지 않는다."""
    if not isinstance(text, str):
        raise VerdictParseError("응답이 문자열이 아니다")
    payload = None
    try:
        payload = json.loads(text.strip())
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                payload = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                payload = None
    if not isinstance(payload, dict):
        raise VerdictParseError("응답에서 verdict JSON 객체를 찾지 못했다")
    try:
        verdict = Verdict(payload.get("verdict"))
    except ValueError:
        raise VerdictParseError("verdict: 허용되지 않는 값") from None
    reason = payload.get("reason")
    if not isinstance(reason, str):
        raise VerdictParseError("reason: 문자열이 아니다")
    return verdict, reason


# --- 교정 레코드 (신규 record_type — 전역 규칙 준수) ---------------------------


@dataclass(frozen=True)
class JudgeCalibration:
    """판정기 교정 검사 결과 (spec §3.3). 원문 응답 없이 기대/실제 요약만 담는다."""

    calibration_id: str
    calibrated_at: datetime
    guard_spec_hash: str
    rubric_hash: str
    model_id: str
    model_settings_hash: str
    examples_total: int
    examples_passed: int
    passed: bool
    failures: tuple[str, ...]
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        if not isinstance(self.calibration_id, str) or not self.calibration_id:
            raise SchemaError("calibration_id: 비어 있지 않은 문자열이어야 한다")
        if (
            not isinstance(self.calibrated_at, datetime)
            or self.calibrated_at.tzinfo is None
            or self.calibrated_at.utcoffset() != timezone.utc.utcoffset(None)
        ):
            raise SchemaError("calibrated_at: UTC aware datetime이어야 한다")
        for name in ("guard_spec_hash", "rubric_hash", "model_id", "model_settings_hash"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise SchemaError(f"{name}: 비어 있지 않은 문자열이어야 한다")
        for name in ("examples_total", "examples_passed"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise SchemaError(f"{name}: 0 이상의 정수여야 한다")
        if self.examples_passed > self.examples_total:
            raise SchemaError("examples_passed: examples_total을 넘을 수 없다")
        if not isinstance(self.passed, bool):
            raise SchemaError("passed: bool이어야 한다")
        expected = self.examples_total > 0 and self.examples_passed == self.examples_total
        if self.passed != expected:
            raise SchemaError("passed: 예시 통과 수와 일치해야 한다")
        if not isinstance(self.failures, tuple) or any(
            not isinstance(f, str) for f in self.failures
        ):
            raise SchemaError("failures: 문자열 튜플이어야 한다")

    @property
    def record_id(self) -> str:
        return self.calibration_id

    @property
    def record_time(self) -> datetime:
        return self.calibrated_at


def calibration_to_json(record: JudgeCalibration) -> dict:
    return {
        "record_type": CALIBRATION_RECORD_TYPE,
        "schema_version": record.schema_version,
        "calibration_id": record.calibration_id,
        "calibrated_at": record.calibrated_at.isoformat(),
        "guard_spec_hash": record.guard_spec_hash,
        "rubric_hash": record.rubric_hash,
        "model_id": record.model_id,
        "model_settings_hash": record.model_settings_hash,
        "examples_total": record.examples_total,
        "examples_passed": record.examples_passed,
        "passed": record.passed,
        "failures": list(record.failures),
    }


_CALIBRATION_KEYS = {
    "schema_version",
    "calibration_id",
    "calibrated_at",
    "guard_spec_hash",
    "rubric_hash",
    "model_id",
    "model_settings_hash",
    "examples_total",
    "examples_passed",
    "passed",
    "failures",
}


def calibration_from_json(payload: dict) -> JudgeCalibration:
    if not isinstance(payload, dict):
        raise SchemaError("judge_calibration: payload는 객체여야 한다")
    if payload.get("record_type") != CALIBRATION_RECORD_TYPE:
        raise SchemaError(
            f"record_type: {CALIBRATION_RECORD_TYPE} 이 아니다 — {payload.get('record_type')!r}"
        )
    keys = set(payload) - {"record_type"}
    if keys != _CALIBRATION_KEYS:
        missing = sorted(_CALIBRATION_KEYS - keys)
        extra = sorted(keys - _CALIBRATION_KEYS)
        raise SchemaError(f"judge_calibration: 키 불일치 (누락 {missing}, 초과 {extra})")
    try:
        calibrated_at = datetime.fromisoformat(payload["calibrated_at"])
    except (TypeError, ValueError) as exc:
        raise SchemaError("calibrated_at: ISO 8601 시각이 아니다") from exc
    try:
        failures = tuple(payload["failures"])
    except TypeError as exc:
        raise SchemaError("failures: 목록이어야 한다") from exc
    return JudgeCalibration(
        calibration_id=payload["calibration_id"],
        calibrated_at=calibrated_at,
        guard_spec_hash=payload["guard_spec_hash"],
        rubric_hash=payload["rubric_hash"],
        model_id=payload["model_id"],
        model_settings_hash=payload["model_settings_hash"],
        examples_total=payload["examples_total"],
        examples_passed=payload["examples_passed"],
        passed=payload["passed"],
        failures=failures,
        schema_version=payload["schema_version"],
    )


def append_calibration(root: Path | str, record: JudgeCalibration) -> None:
    """교정 레코드 append — 주 저장소와 같은 원자 append·락 규율."""
    root = Path(root)
    line = json.dumps(
        calibration_to_json(record), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    data = line.encode("utf-8") + b"\n"
    root.mkdir(parents=True, exist_ok=True)
    fd = os.open(root / CALIBRATION_FILENAME, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def load_calibrations(root: Path | str) -> tuple[list[JudgeCalibration], int]:
    """교정 레코드 로드. 손상 줄은 원문 없이 건수만 센다."""
    path = Path(root) / CALIBRATION_FILENAME
    if not path.exists():
        return [], 0
    records: list[JudgeCalibration] = []
    corrupt = 0
    for raw in path.read_bytes().split(b"\n"):
        if not raw:
            continue
        try:
            records.append(calibration_from_json(json.loads(raw.decode("utf-8"))))
        except (UnicodeDecodeError, json.JSONDecodeError, SchemaError):
            corrupt += 1
    return records, corrupt


def find_calibration(
    records: list[JudgeCalibration],
    *,
    guard_spec_hash: str,
    rubric_hash: str,
    model_id: str,
    model_settings_hash: str,
) -> JudgeCalibration | None:
    """같은 (spec, 루브릭, 모델, 설정)의 최신 교정 레코드."""
    match = None
    for record in records:
        if (
            record.guard_spec_hash == guard_spec_hash
            and record.rubric_hash == rubric_hash
            and record.model_id == model_id
            and record.model_settings_hash == model_settings_hash
        ):
            match = record
    return match


# --- 판정 대상 선택 -----------------------------------------------------------


@dataclass(frozen=True)
class JudgePlan:
    """판정 실행 계획 — 비용 승인 게이트가 사용자에게 보여줄 자료."""

    pending_event_ids: tuple[str, ...]
    rejudge_event_ids: tuple[str, ...]
    spec_keys: tuple[tuple[str, int], ...]
    already_judged: int
    unregistered_count: int  # 판정 대상 아님 — 건수만 보고 (참조 spec 부재)
    test_count: int  # 판정 대상 아님 — 비운영
    unknown_count: int  # 판정 대상 아님 — 비운영
    unjudgeable_event_ids: tuple[str, ...]  # 참조 spec 없음/해시 불일치 — 호출 없이 보고


def build_plan(dataset: Dataset, *, rejudge: tuple[str, ...] = ()) -> JudgePlan:
    """새 `operation` 사건 중 PolicyVerdict가 없는 전부를 고른다.

    출처는 amendment를 적용한 유효 출처 기준이다 — `test` 강등 사건은 대상이
    아니다. 재판정 지정 사건은 기존 verdict가 있어도 대상에 넣는다.
    """
    pending: list[str] = []
    rejudge_ids: list[str] = []
    unjudgeable: list[str] = []
    spec_keys: list[tuple[str, int]] = []
    already = unregistered = test = unknown = 0
    rejudge_set = set(rejudge)
    matched_rejudge: set[str] = set()

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
        key = (event.guard_id, event.guard_version)
        spec = dataset.specs_by_key.get(key)
        if spec is None or spec.content_hash != event.guard_spec_hash:
            unjudgeable.append(event_id)
            continue
        has_verdict = dataset.latest_verdict(event_id) is not None
        if not has_verdict:
            pending.append(event_id)
            if event_id in rejudge_set:
                # 미처리 사건의 재판정 지정은 일반 판정으로 흡수한다.
                matched_rejudge.add(event_id)
            if key not in spec_keys:
                spec_keys.append(key)
        elif event_id in rejudge_set:
            pending.append(event_id)
            rejudge_ids.append(event_id)
            matched_rejudge.add(event_id)
            if key not in spec_keys:
                spec_keys.append(key)
        else:
            already += 1

    missing = sorted(rejudge_set - matched_rejudge)
    if missing:
        raise JudgeError(f"재판정 대상이 판정 가능한 사건이 아니다: {missing}")

    return JudgePlan(
        pending_event_ids=tuple(pending),
        rejudge_event_ids=tuple(rejudge_ids),
        spec_keys=tuple(spec_keys),
        already_judged=already,
        unregistered_count=unregistered,
        test_count=test,
        unknown_count=unknown,
        unjudgeable_event_ids=tuple(unjudgeable),
    )


def billing_plan(
    store: AppendStore,
    *,
    model_id: str = DEFAULT_MODEL_ID,
    model_settings: dict | None = None,
    calibrate: bool = True,
    rejudge: tuple[str, ...] = (),
) -> dict:
    """비용 승인 게이트용 계획 요약 — 판정 대상 건수·모델·예상 호출 수."""
    settings = dict(DEFAULT_MODEL_SETTINGS) if model_settings is None else dict(model_settings)
    settings_hash = value_hash(settings)
    dataset = Dataset(store.load().records)
    plan = build_plan(dataset, rejudge=rejudge)
    current_rubric = rubric_hash()
    existing, _ = load_calibrations(store.root)
    calibration_calls = 0
    if calibrate:
        for key in plan.spec_keys:
            spec = dataset.specs_by_key[key]
            if (
                find_calibration(
                    existing,
                    guard_spec_hash=spec.content_hash,
                    rubric_hash=current_rubric,
                    model_id=model_id,
                    model_settings_hash=settings_hash,
                )
                is None
            ):
                calibration_calls += len(calibration_cases(spec))
    return {
        "model_id": model_id,
        "model_settings": settings,
        "pending_event_ids": list(plan.pending_event_ids),
        "rejudge_event_ids": list(plan.rejudge_event_ids),
        "already_judged": plan.already_judged,
        "excluded": {
            "unregistered": plan.unregistered_count,
            "test": plan.test_count,
            "unknown": plan.unknown_count,
        },
        "unjudgeable_event_ids": list(plan.unjudgeable_event_ids),
        "calibration_calls": calibration_calls,
        "planned_llm_calls": len(plan.pending_event_ids) + calibration_calls,
    }


# --- 판정 실행 ---------------------------------------------------------------


@dataclass(frozen=True)
class JudgedEvent:
    event_id: str
    verdict_id: str
    verdict: Verdict


@dataclass(frozen=True)
class CalibrationOutcome:
    guard_id: str
    guard_version: int
    record: JudgeCalibration
    reused: bool


@dataclass(frozen=True)
class JudgeRunResult:
    plan: JudgePlan
    model_id: str
    model_settings_hash: str
    rubric_hash: str
    judged: tuple[JudgedEvent, ...]
    failed_event_ids: tuple[str, ...]  # 미처리 유지 — LossRecord만 남는다
    calibrations: tuple[CalibrationOutcome, ...]


def _run_calibration(
    *,
    transport: JudgeTransport,
    spec: GuardSpec,
    model_id: str,
    settings: dict,
    settings_hash: str,
    current_rubric: str,
    now: datetime,
) -> JudgeCalibration:
    cases = calibration_cases(spec)
    passed_count = 0
    failures: list[str] = []
    for case in cases:
        label = f"{case.kind}[{case.index}]"
        messages = render_messages(build_calibration_bundle(spec, case))
        try:
            actual, _ = parse_verdict_response(
                transport.complete(model_id=model_id, messages=messages, settings=settings)
            )
        except (TransportError, VerdictParseError) as exc:
            failures.append(f"{label}: {type(exc).__name__}")
            continue
        if actual is case.expected:
            passed_count += 1
        else:
            failures.append(f"{label}: 기대 {case.expected.value}, 실제 {actual.value}")
    total = len(cases)
    return JudgeCalibration(
        calibration_id=f"cal-{uuid.uuid4().hex}",
        calibrated_at=now,
        guard_spec_hash=spec.content_hash,
        rubric_hash=current_rubric,
        model_id=model_id,
        model_settings_hash=settings_hash,
        examples_total=total,
        examples_passed=passed_count,
        passed=total > 0 and passed_count == total,
        failures=tuple(failures),
    )


def run_judge(
    store: AppendStore,
    *,
    transport: JudgeTransport,
    model_id: str = DEFAULT_MODEL_ID,
    model_settings: dict | None = None,
    calibrate: bool = True,
    rejudge: tuple[str, ...] = (),
    rejudge_reason: str | None = None,
    now: datetime | None = None,
) -> JudgeRunResult:
    """판정 대상 전부에 사건당 독립 LLM 1회 호출로 verdict를 append한다.

    비용 승인 게이트는 호출자(CLI) 소관이다 — 이 함수는 이미 승인된 실행이다.
    """
    if rejudge and not (isinstance(rejudge_reason, str) and rejudge_reason.strip()):
        raise JudgeError("재판정에는 사유(rejudge_reason)가 필수다")
    settings = dict(DEFAULT_MODEL_SETTINGS) if model_settings is None else dict(model_settings)
    settings_hash = value_hash(settings)
    current_rubric = rubric_hash()
    judged_at = now if now is not None else datetime.now(timezone.utc)

    dataset = Dataset(store.load().records)
    plan = build_plan(dataset, rejudge=rejudge)
    rejudge_ids = set(plan.rejudge_event_ids)

    # 교정 — 실제 판정보다 먼저. 같은 설정의 기존 레코드는 재사용한다.
    calibrations: list[CalibrationOutcome] = []
    calibration_by_key: dict[tuple[str, int], JudgeCalibration | None] = {}
    if calibrate:
        existing, _ = load_calibrations(store.root)
        for key in plan.spec_keys:
            spec = dataset.specs_by_key[key]
            record = find_calibration(
                existing,
                guard_spec_hash=spec.content_hash,
                rubric_hash=current_rubric,
                model_id=model_id,
                model_settings_hash=settings_hash,
            )
            reused = record is not None
            if record is None:
                record = _run_calibration(
                    transport=transport,
                    spec=spec,
                    model_id=model_id,
                    settings=settings,
                    settings_hash=settings_hash,
                    current_rubric=current_rubric,
                    now=judged_at,
                )
                append_calibration(store.root, record)
            calibration_by_key[key] = record
            calibrations.append(
                CalibrationOutcome(
                    guard_id=key[0], guard_version=key[1], record=record, reused=reused
                )
            )
    else:
        for key in plan.spec_keys:
            calibration_by_key[key] = None  # 교정 미실시 설정

    # 판정 — 사건당 독립 1회 호출. 실패는 미처리 유지 + LossRecord.
    judged: list[JudgedEvent] = []
    failed: list[str] = []
    for event_id in plan.pending_event_ids:
        event = dataset.events[event_id]
        key = (event.guard_id, event.guard_version)
        spec = dataset.specs_by_key[key]
        bundle = build_context_bundle(event, spec)
        messages = render_messages(bundle)
        try:
            text = transport.complete(model_id=model_id, messages=messages, settings=settings)
            verdict, model_reason = parse_verdict_response(text)
        except (TransportError, VerdictParseError) as exc:
            failed.append(event_id)
            store.append(
                LossRecord(
                    loss_id=f"loss-{uuid.uuid4().hex}",
                    recorded_at=judged_at,
                    kind=LossKind.VERDICT_FAILURE,
                    detail=f"policy_verdict 실패: {type(exc).__name__}"[:500],
                    subject_ref=event_id,
                )
            )
            continue

        notes: list[str] = []
        calibration = calibration_by_key.get(key)
        if calibration is None:
            notes.append(NOTE_CALIBRATION_NOT_RUN)
        elif not calibration.passed:
            notes.append(NOTE_CALIBRATION_FAILED)
        if event_id in rejudge_ids:
            notes.append(f"[재판정: {rejudge_reason.strip()}]")
        reason = scrub_text(model_reason).strip()
        if len(reason) > _MAX_REASON:
            reason = reason[:_MAX_REASON] + "…[truncated]"
        if notes:
            reason = " ".join(notes + [reason]) if reason else " ".join(notes)

        record = PolicyVerdict(
            verdict_id=f"vd-{uuid.uuid4().hex}",
            event_id=event_id,
            verdict=verdict,
            reason=reason,
            context_bundle_hash=context_bundle_hash(bundle),
            guard_spec_hash=spec.content_hash,
            rubric_hash=current_rubric,
            model_id=model_id,
            model_settings_hash=settings_hash,
            judged_at=judged_at,
        )
        store.append(record)
        judged.append(
            JudgedEvent(event_id=event_id, verdict_id=record.verdict_id, verdict=verdict)
        )

    return JudgeRunResult(
        plan=plan,
        model_id=model_id,
        model_settings_hash=settings_hash,
        rubric_hash=current_rubric,
        judged=tuple(judged),
        failed_event_ids=tuple(failed),
        calibrations=tuple(calibrations),
    )
