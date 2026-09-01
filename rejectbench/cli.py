"""GuardSpec 작성·등록·조회 CLI (T2) + 세션 뒤 정책 판정 (T4) +
사용자 검토·가드 결정 (T5) + 보고서 (T6). 표준 라이브러리만 사용한다.

    python -m rejectbench.cli validate --file draft.json
    python -m rejectbench.cli register --store DIR --file draft.json \
        [--enforcement-script PATH]
    python -m rejectbench.cli list --store DIR
    python -m rejectbench.cli show --store DIR --guard GUARD_ID [--version N]
    python -m rejectbench.cli judge --store DIR [--model MODEL] \
        [--approve-billing] [--skip-calibration] \
        [--rejudge EVENT_ID --rejudge-reason 사유]
    python -m rejectbench.cli review list --store DIR
    python -m rejectbench.cli review record --store DIR --event EVENT_ID \
        --utility useful|unnecessary|uncertain [--note TEXT]
    python -m rejectbench.cli review demote --store DIR --event EVENT_ID --reason 사유
    python -m rejectbench.cli guard show GUARD_ID --store DIR
    python -m rejectbench.cli decide --store DIR --guard GUARD_ID \
        --decision keep|modify|remove --evidence EVENT_ID [--evidence ...] \
        --rationale TEXT [--modify-file draft.json] [--enforcement-script PATH]
    python -m rejectbench.cli decisions --store DIR --guard GUARD_ID
    python -m rejectbench.cli report --store DIR [--out [경로]]

report는 지표·병기 상태를 담은 Markdown 보고서를 stdout으로 낸다. `--out`만
주면 store 루트 하위 `reports/report-<UTC시각>.md` 기본 경로에, `--out 경로`는
그 경로에 파일로 쓴다. 보고서에는 홈 경로·세션 식별자를 넣지 않는다.

draft JSON은 의미 필드만 담는다 — version·시각·해시는 등록부가 정한다:
`guard_id`, `project`, `purpose`, `policy`, `exceptions`(선택, 기본 []),
`allow_examples`, `block_examples`.

judge는 비용 승인 게이트가 기본 dry-run이다: `--approve-billing` 플래그나
`REJECTBENCH_BILLING_APPROVED=1` 없이는 판정 대상 목록만 출력하고 과금 호출을
한 건도 하지 않는다. API 키는 `OPENAI_API_KEY` 환경변수로만 읽는다.

review·decide에는 LLM이 없다 — 전부 사용자 입력이다. `review list`는 전수
검토 큐라서 사건을 선별해 빼는 옵션이 없고, `guard show`는 로컬 stdout 출력
뿐이며 파일로 내보내는 경로를 만들지 않는다.

종료 코드: 0 성공(dry-run 포함), 1 검증·등록부·판정·검토·결정 오류(stderr에
사유), 2 사용법 오류.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from rejectbench import judge as judge_module
from rejectbench.dataset import Dataset
from rejectbench.decision import (
    DecisionError,
    annotate_decision,
    build_guard_view,
    decision_history,
    record_decision,
    record_modify,
    render_guard_view,
)
from rejectbench.hashing import content_hash
from rejectbench.judge import BILLING_ENV, JudgeError
from rejectbench.records import Decision, SchemaError, Utility, to_json
from rejectbench.registry import (
    GuardRegistry,
    RegistryError,
    check_quality,
    enforcement_ref_for,
)
from rejectbench.report import default_report_path, generate_report
from rejectbench.review import ReviewError, demote_to_test, record_review, review_queue
from rejectbench.store import AppendStore, production_root

_REQUIRED_KEYS = ("guard_id", "project", "purpose", "policy", "allow_examples", "block_examples")
_OPTIONAL_KEYS = ("exceptions",)


class DraftError(ValueError):
    """draft 파일이 계약과 다르다."""


def _load_draft(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DraftError(f"draft 파일이 없다: {path}") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DraftError(f"draft 파일을 읽을 수 없다: {path} — {exc}") from exc
    if not isinstance(payload, dict):
        raise DraftError("draft: JSON 객체여야 한다")
    allowed = set(_REQUIRED_KEYS) | set(_OPTIONAL_KEYS)
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise DraftError(
            f"draft: 허용되지 않는 키 {unknown} — version·시각·해시는 등록부가 정한다"
        )
    missing = sorted(k for k in _REQUIRED_KEYS if k not in payload)
    if missing:
        raise DraftError(f"draft: 필수 키 누락 {missing}")
    draft = {"exceptions": [], **payload}
    for key in ("guard_id", "project", "purpose", "policy"):
        if not isinstance(draft[key], str):
            raise DraftError(f"draft.{key}: 문자열이어야 한다")
    for key in ("exceptions", "allow_examples", "block_examples"):
        value = draft[key]
        if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
            raise DraftError(f"draft.{key}: 문자열 목록이어야 한다")
        draft[key] = tuple(value)
    if not draft["guard_id"].strip():
        raise DraftError("draft.guard_id: 비어 있지 않아야 한다")
    if not draft["project"].strip():
        raise DraftError("draft.project: 비어 있지 않아야 한다")
    return draft


def _print_json(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _cmd_validate(args: argparse.Namespace) -> int:
    draft = _load_draft(Path(args.file))
    check_quality(
        policy=draft["policy"],
        exceptions=draft["exceptions"],
        allow_examples=draft["allow_examples"],
        block_examples=draft["block_examples"],
    )
    _print_json(
        {
            "valid": True,
            "guard_id": draft["guard_id"],
            "content_hash": content_hash(
                purpose=draft["purpose"],
                policy=draft["policy"],
                exceptions=draft["exceptions"],
                allow_examples=draft["allow_examples"],
                block_examples=draft["block_examples"],
            ),
        }
    )
    return 0


def _cmd_register(args: argparse.Namespace) -> int:
    draft = _load_draft(Path(args.file))
    enforcement_ref = (
        enforcement_ref_for(args.enforcement_script) if args.enforcement_script else None
    )
    registry = GuardRegistry(AppendStore(args.store))
    result = registry.register(
        guard_id=draft["guard_id"],
        project=draft["project"],
        purpose=draft["purpose"],
        policy=draft["policy"],
        exceptions=draft["exceptions"],
        allow_examples=draft["allow_examples"],
        block_examples=draft["block_examples"],
        enforcement_ref=enforcement_ref,
    )
    _print_json(
        {
            "guard_id": result.spec.guard_id,
            "version": result.spec.version,
            "spec_id": result.spec.spec_id,
            "content_hash": result.spec.content_hash,
            "created": result.created,
        }
    )
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    registry = GuardRegistry(AppendStore(args.store))
    _print_json(
        [
            {
                "guard_id": guard_id,
                "latest_version": registry.latest(guard_id).version,
                "versions": [spec.version for spec in registry.versions(guard_id)],
            }
            for guard_id in registry.guard_ids()
        ]
    )
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    registry = GuardRegistry(AppendStore(args.store))
    if args.version is None:
        spec = registry.latest(args.guard)
    else:
        spec = registry.get(args.guard, args.version)
    _print_json(to_json(spec))
    return 0


def _parse_model_settings(raw: str | None) -> dict | None:
    """`--model-settings` JSON을 판정 설정 딕셔너리로 읽는다.

    전체 교체 의미다 — `model_settings_hash`가 실제로 쓰인 설정을 가리켜야
    하므로 명령줄에 적은 것이 곧 해시 대상이 된다. `None`이면 기본값을 쓴다.
    """
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise JudgeError(f"--model-settings: JSON으로 파싱할 수 없다 — {exc.msg}") from None
    if not isinstance(parsed, dict):
        raise JudgeError("--model-settings: JSON 객체여야 한다 (전체 교체 — 병합이 아니다)")
    return parsed


def _cmd_judge(args: argparse.Namespace) -> int:
    store = AppendStore(args.store)
    rejudge = tuple(args.rejudge or ())
    calibrate = not args.skip_calibration
    # 과금 경로·전송 계층 생성보다 먼저 검증한다 — 잘못된 설정으로 호출하지 않는다.
    model_settings = _parse_model_settings(args.model_settings)
    summary = judge_module.billing_plan(
        store,
        model_id=args.model,
        model_settings=model_settings,
        calibrate=calibrate,
        rejudge=rejudge,
    )
    approved = bool(args.approve_billing) or os.environ.get(BILLING_ENV) == "1"
    if not approved:
        # 비용 승인 게이트 — 대상 목록만 출력하고 한 건도 호출하지 않는다.
        _print_json(
            {
                "approved": False,
                "note": "승인 없음 — dry-run. --approve-billing 또는 "
                f"{BILLING_ENV}=1 로만 과금 호출을 승인한다",
                **summary,
            }
        )
        return 0
    if summary["planned_llm_calls"] == 0:
        _print_json({"approved": True, "judged": [], "failed_event_ids": [], **summary})
        return 0
    transport = judge_module.OpenAITransport()
    result = judge_module.run_judge(
        store,
        transport=transport,
        model_id=args.model,
        model_settings=model_settings,
        calibrate=calibrate,
        rejudge=rejudge,
        rejudge_reason=args.rejudge_reason,
    )
    _print_json(
        {
            "approved": True,
            **summary,
            "judged": [
                {"event_id": j.event_id, "verdict_id": j.verdict_id, "verdict": j.verdict.value}
                for j in result.judged
            ],
            "failed_event_ids": list(result.failed_event_ids),
            "calibrations": [
                {
                    "guard_id": c.guard_id,
                    "guard_version": c.guard_version,
                    "passed": c.record.passed,
                    "reused": c.reused,
                }
                for c in result.calibrations
            ],
        }
    )
    return 0


def _load_store_dataset(store_root: str) -> tuple[AppendStore, Dataset]:
    store = AppendStore(store_root)
    return store, Dataset(store.load().records)


def _cmd_review_list(args: argparse.Namespace) -> int:
    _, dataset = _load_store_dataset(args.store)
    queue = review_queue(dataset)
    pending = []
    for event_id in queue.pending_event_ids:
        event = dataset.events[event_id]
        pending.append(
            {
                "event_id": event_id,
                "occurred_at": event.occurred_at.isoformat(),
                "session_id": event.session_id,
                "guard_id": event.guard_id,
                "guard_version": event.guard_version,
                "capture_status": event.capture_status.value,
                "reason": event.reason,
            }
        )
    _print_json(
        {
            "pending": pending,
            "counts": {
                "pending": len(pending),
                "reviewed": queue.reviewed,
                "test": queue.test,
                "unknown": queue.unknown,
                "unregistered": queue.unregistered,
            },
        }
    )
    return 0


def _cmd_review_record(args: argparse.Namespace) -> int:
    review = record_review(
        AppendStore(args.store),
        event_id=args.event,
        utility=Utility(args.utility),
        note=args.note,
    )
    _print_json(
        {
            "review_id": review.review_id,
            "event_id": review.event_id,
            "utility": review.utility.value,
            "note": review.note,
            "reviewed_at": review.reviewed_at.isoformat(),
        }
    )
    return 0


def _cmd_review_demote(args: argparse.Namespace) -> int:
    amendment = demote_to_test(
        AppendStore(args.store), event_id=args.event, reason=args.reason
    )
    _print_json(
        {
            "amendment_id": amendment.amendment_id,
            "event_id": amendment.target_id,
            "field": amendment.field,
            "new_value": amendment.new_value,
            "reason": amendment.reason,
            "amended_at": amendment.amended_at.isoformat(),
        }
    )
    return 0


def _cmd_guard_show(args: argparse.Namespace) -> int:
    _, dataset = _load_store_dataset(args.store)
    print(render_guard_view(build_guard_view(dataset, args.guard_id)))
    return 0


def _decision_payload(outcome) -> dict:
    decision = outcome.decision
    annotation = outcome.annotation
    return {
        "decision_id": decision.decision_id,
        "guard_id": decision.guard_id,
        "decision": decision.decision.value,
        "evidence_event_ids": list(decision.evidence_event_ids),
        "rationale": decision.rationale,
        "decided_at": decision.decided_at.isoformat(),
        "resulting_guard_version": decision.resulting_guard_version,
        "countable": annotation.countable,
        "no_event_guard": annotation.no_event_guard,
        "reasons": list(annotation.reasons),
    }


def _cmd_decide(args: argparse.Namespace) -> int:
    store = AppendStore(args.store)
    decision = Decision(args.decision)
    evidence = tuple(args.evidence or ())
    if decision is Decision.MODIFY:
        if not args.modify_file:
            raise DecisionError(
                "modify 결정에는 --modify-file(새 GuardSpec draft)이 필수다"
            )
        draft = _load_draft(Path(args.modify_file))
        if draft["guard_id"] != args.guard:
            raise DecisionError(
                f"draft.guard_id({draft['guard_id']})가 --guard({args.guard})와 다르다"
            )
        enforcement_ref = (
            enforcement_ref_for(args.enforcement_script)
            if args.enforcement_script
            else None
        )
        outcome = record_modify(
            store,
            guard_id=args.guard,
            evidence_event_ids=evidence,
            rationale=args.rationale,
            project=draft["project"],
            purpose=draft["purpose"],
            policy=draft["policy"],
            exceptions=draft["exceptions"],
            allow_examples=draft["allow_examples"],
            block_examples=draft["block_examples"],
            enforcement_ref=enforcement_ref,
        )
        _print_json(
            {
                **_decision_payload(outcome),
                "enforcement": {
                    "status": outcome.enforcement.status.value,
                    "detail": outcome.enforcement.detail,
                },
            }
        )
        return 0
    if args.modify_file or args.enforcement_script:
        raise DecisionError("--modify-file/--enforcement-script는 modify 결정 전용이다")
    outcome = record_decision(
        store,
        guard_id=args.guard,
        decision=decision,
        evidence_event_ids=evidence,
        rationale=args.rationale,
    )
    _print_json(_decision_payload(outcome))
    return 0


_DEFAULT_OUT = ""  # `--out`만 준 경우의 sentinel — store 기본 경로를 쓴다


def _cmd_report(args: argparse.Namespace) -> int:
    store = AppendStore(args.store)
    markdown = generate_report(store)
    if args.out is None:
        print(markdown, end="")
        return 0
    out_path = (
        default_report_path(store) if args.out == _DEFAULT_OUT else Path(args.out)
    )
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(markdown, encoding="utf-8")
    except OSError as exc:
        print(f"오류: 보고서 파일을 쓸 수 없다 — {exc}", file=sys.stderr)
        return 1
    _print_json({"written": str(out_path), "bytes": len(markdown.encode("utf-8"))})
    return 0


def _cmd_decisions(args: argparse.Namespace) -> int:
    _, dataset = _load_store_dataset(args.store)
    if not any(gid == args.guard for gid, _ in dataset.specs_by_key):
        raise DecisionError(f"등록되지 않은 가드: {args.guard}")
    _print_json(
        [
            {
                "decision_id": decision.decision_id,
                "decision": decision.decision.value,
                "evidence_event_ids": list(decision.evidence_event_ids),
                "rationale": decision.rationale,
                "decided_at": decision.decided_at.isoformat(),
                "resulting_guard_version": decision.resulting_guard_version,
                "countable": annotate_decision(dataset, decision).countable,
                "no_event_guard": annotate_decision(dataset, decision).no_event_guard,
            }
            for decision in decision_history(dataset, args.guard)
        ]
    )
    return 0


def _add_store_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--store",
        default=str(production_root()),
        help="등록부 store 디렉터리 (기본: 운영 data/v7 — 테스트는 반드시 임시 경로)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m rejectbench.cli",
        description="버전 고정 GuardSpec 등록부 CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="draft 파일을 쓰지 않고 검증한다")
    validate.add_argument("--file", required=True, help="GuardSpec draft JSON 경로")
    validate.set_defaults(func=_cmd_validate)

    register = sub.add_parser("register", help="draft를 검증해 등록부에 append한다")
    _add_store_option(register)
    register.add_argument("--file", required=True, help="GuardSpec draft JSON 경로")
    register.add_argument(
        "--enforcement-script",
        help="가드 스크립트 경로 — 바이트 SHA-256만 계산하며 절대 실행하지 않는다",
    )
    register.set_defaults(func=_cmd_register)

    list_parser = sub.add_parser("list", help="등록된 가드와 버전을 나열한다")
    _add_store_option(list_parser)
    list_parser.set_defaults(func=_cmd_list)

    show = sub.add_parser("show", help="spec 하나를 JSON으로 출력한다")
    _add_store_option(show)
    show.add_argument("--guard", required=True, help="guard_id")
    show.add_argument("--version", type=int, help="버전 (기본: 최신)")
    show.set_defaults(func=_cmd_show)

    judge = sub.add_parser(
        "judge",
        help="새 operation 사건을 LLM으로 정책 판정한다 (승인 없이는 dry-run)",
    )
    _add_store_option(judge)
    judge.add_argument(
        "--model",
        default=judge_module.DEFAULT_MODEL_ID,
        help=f"판정 모델 (기본: {judge_module.DEFAULT_MODEL_ID})",
    )
    judge.add_argument(
        "--model-settings",
        metavar="JSON",
        help=(
            "판정 모델 설정을 JSON 객체로 **전체 교체**한다 (병합 아님). "
            f"기본값 {judge_module.DEFAULT_MODEL_SETTINGS}를 모델이 거부할 때 쓴다 — "
            "예: '{}' (temperature 키 자체를 뺀다). "
            "바뀐 설정은 model_settings_hash에 반영되어 재판정 규율을 따른다"
        ),
    )
    judge.add_argument(
        "--approve-billing",
        action="store_true",
        help=f"과금 호출 명시 승인 (env {BILLING_ENV}=1 로도 승인 가능)",
    )
    judge.add_argument(
        "--skip-calibration",
        action="store_true",
        help="판정기 교정 생략 — 판정에 '교정 미실시'가 병기된다",
    )
    judge.add_argument(
        "--rejudge",
        action="append",
        default=[],
        metavar="EVENT_ID",
        help="재판정할 사건 id (반복 가능) — 이전 판정은 보존된다",
    )
    judge.add_argument("--rejudge-reason", help="재판정 사유 (--rejudge 사용 시 필수)")
    judge.set_defaults(func=_cmd_judge)

    review = sub.add_parser("review", help="새 operation 사건의 전수 유용성 검토")
    review_sub = review.add_subparsers(dest="review_command", required=True)

    review_list = review_sub.add_parser(
        "list",
        help="검토 큐 전부를 나열한다 — 사건을 선별해 빼는 옵션은 없다",
    )
    _add_store_option(review_list)
    review_list.set_defaults(func=_cmd_review_list)

    review_record = review_sub.add_parser("record", help="사건 하나의 검토를 append한다")
    _add_store_option(review_record)
    review_record.add_argument("--event", required=True, help="event_id")
    review_record.add_argument(
        "--utility",
        required=True,
        choices=[u.value for u in Utility],
        help="useful | unnecessary | uncertain(기록된 보류값)",
    )
    review_record.add_argument("--note", default="", help="검토 메모 (선택)")
    review_record.set_defaults(func=_cmd_review_record)

    review_demote = review_sub.add_parser(
        "demote",
        help="시험·강제 발동으로 확인된 사건을 사유 있는 amendment로 test 강등한다",
    )
    _add_store_option(review_demote)
    review_demote.add_argument("--event", required=True, help="event_id")
    review_demote.add_argument("--reason", required=True, help="강등 사유 (필수)")
    review_demote.set_defaults(func=_cmd_review_demote)

    guard = sub.add_parser("guard", help="가드별 최소 뷰")
    guard_sub = guard.add_subparsers(dest="guard_command", required=True)
    guard_show = guard_sub.add_parser(
        "show",
        help="세션 수·사건·정책/유용성 두 축·판정 가능 상태를 한 화면 텍스트로",
    )
    guard_show.add_argument("guard_id", help="guard_id")
    _add_store_option(guard_show)
    guard_show.set_defaults(func=_cmd_guard_show)

    decide = sub.add_parser(
        "decide",
        help="keep | modify | remove 결정을 근거 사건과 함께 append한다",
    )
    _add_store_option(decide)
    decide.add_argument("--guard", required=True, help="guard_id")
    decide.add_argument(
        "--decision", required=True, choices=[d.value for d in Decision]
    )
    decide.add_argument(
        "--evidence",
        action="append",
        default=[],
        metavar="EVENT_ID",
        help="근거 사건 id (반복 가능) — 두 판단 모두 확정값인 operation 사건만",
    )
    decide.add_argument("--rationale", required=True, help="결정 사유")
    decide.add_argument(
        "--modify-file",
        help="modify 전용: 새 GuardSpec draft JSON — 등록부 경유 새 버전 생성을 강제한다",
    )
    decide.add_argument(
        "--enforcement-script",
        help="modify 전용: 가드 스크립트 경로 — 해시 대조용, 절대 실행하지 않는다",
    )
    decide.set_defaults(func=_cmd_decide)

    decisions = sub.add_parser("decisions", help="가드 하나의 결정 이력 (append 순서)")
    _add_store_option(decisions)
    decisions.add_argument("--guard", required=True, help="guard_id")
    decisions.set_defaults(func=_cmd_decisions)

    report = sub.add_parser(
        "report",
        help="지표·병기 상태 Markdown 보고서 — stdout 또는 --out 파일",
    )
    _add_store_option(report)
    report.add_argument(
        "--out",
        nargs="?",
        const=_DEFAULT_OUT,
        default=None,
        metavar="경로",
        help=(
            "보고서를 파일로 쓴다. 경로 없이 주면 store 루트 하위 "
            "reports/report-<UTC시각>.md 기본 경로를 쓴다"
        ),
    )
    report.set_defaults(func=_cmd_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (
        DraftError,
        RegistryError,
        SchemaError,
        JudgeError,
        ReviewError,
        DecisionError,
    ) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
