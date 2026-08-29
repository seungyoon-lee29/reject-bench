"""GuardSpec 작성·등록·조회 CLI (T2) + 세션 뒤 정책 판정 (T4). 표준 라이브러리만 사용한다.

    python -m rejectbench.cli validate --file draft.json
    python -m rejectbench.cli register --store DIR --file draft.json \
        [--enforcement-script PATH]
    python -m rejectbench.cli list --store DIR
    python -m rejectbench.cli show --store DIR --guard GUARD_ID [--version N]
    python -m rejectbench.cli judge --store DIR [--model MODEL] \
        [--approve-billing] [--skip-calibration] \
        [--rejudge EVENT_ID --rejudge-reason 사유]

draft JSON은 의미 필드만 담는다 — version·시각·해시는 등록부가 정한다:
`guard_id`, `project`, `purpose`, `policy`, `exceptions`(선택, 기본 []),
`allow_examples`, `block_examples`.

judge는 비용 승인 게이트가 기본 dry-run이다: `--approve-billing` 플래그나
`REJECTBENCH_BILLING_APPROVED=1` 없이는 판정 대상 목록만 출력하고 과금 호출을
한 건도 하지 않는다. API 키는 `OPENAI_API_KEY` 환경변수로만 읽는다.

종료 코드: 0 성공(dry-run 포함), 1 검증·등록부·판정 오류(stderr에 사유), 2 사용법 오류.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from rejectbench import judge as judge_module
from rejectbench.hashing import content_hash
from rejectbench.judge import BILLING_ENV, JudgeError
from rejectbench.records import SchemaError, to_json
from rejectbench.registry import (
    GuardRegistry,
    RegistryError,
    check_quality,
    enforcement_ref_for,
)
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


def _cmd_judge(args: argparse.Namespace) -> int:
    store = AppendStore(args.store)
    rejudge = tuple(args.rejudge or ())
    calibrate = not args.skip_calibration
    summary = judge_module.billing_plan(
        store, model_id=args.model, calibrate=calibrate, rejudge=rejudge
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

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (DraftError, RegistryError, SchemaError, JudgeError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
