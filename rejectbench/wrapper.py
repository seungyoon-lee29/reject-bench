"""가드 래퍼 — `python -m rejectbench.wrapper --guard <실제 가드 경로>`.

배선 시 settings.json이 가드 대신 이 래퍼를 부른다. 래퍼는:

1. stdin 훅 페이로드를 캡처하고,
2. 실제 가드 스크립트를 같은 페이로드·인자로 실행하고 (타임아웃 없음),
3. 가드의 stdout/stderr/exit code를 그대로 투명 전달하며,
4. 차단으로 판정되면(exit 2 또는 permissionDecision=deny 출력) GuardEvent를
   최선 노력으로 기록한다 — 기록 실패는 가드의 원래 결과를 절대 바꾸지 않는다.

가드 스크립트 실행은 이 도구의 역할 자체다(배선된 가드의 대리 실행). 그 외
어떤 레코드 필드도 실행하지 않는다.

실패 방향: 가드를 실행할 수 없으면(경로 없음·exec 실패) exit 1로 끝낸다 —
Claude Code PreToolUse에서 exit 1은 차단이 아니라 보이는 비블로킹 오류다.
래퍼 결함이 가드가 아닌 것을 차단(exit 2)하게 두지 않는다. 사용법 오류도
같은 이유로 exit 1로 변환한다.

`--harness`는 실행기 중립 입구다: 세션 표기 접두와 페이로드 해석기를 고른다.
현재 해석기는 Claude Code(`claude`)뿐이고, 다른 값은 페이로드를 해석하지
않은 채 unknown/no_context로 기록만 시도한다 — Codex 전용 파싱은 넣지 않는다.
인식하지 못한 인자는 가드에 그대로 전달한다.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from rejectbench.recorder import GuardResult, record_guard_result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m rejectbench.wrapper",
        description="가드 스크립트를 투명하게 대리 실행하고 차단 사건을 기록한다",
        add_help=True,
    )
    parser.add_argument("--guard", required=True, help="실제 가드 스크립트 경로")
    parser.add_argument(
        "--harness",
        default="claude",
        help="실행기 표기 (기본 claude — 페이로드 해석기가 구현된 유일한 값)",
    )
    return parser


def run_guard(guard: Path, guard_args: list[str], payload: bytes) -> subprocess.CompletedProcess:
    """가드를 그 페이로드로 실행한다. 타임아웃 없음 — 결과를 그대로 기다린다."""
    argv = [str(guard), *guard_args]
    if not os.access(guard, os.X_OK):
        argv = ["/bin/bash", str(guard), *guard_args]
    try:
        return subprocess.run(argv, input=payload, capture_output=True)
    except OSError:
        # 셔뱅·포맷 문제 — 관측 대상 가드는 bash 스크립트이므로 bash로 재시도.
        return subprocess.run(
            ["/bin/bash", str(guard), *guard_args], input=payload, capture_output=True
        )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args, guard_args = parser.parse_known_args(argv)
    except SystemExit as exc:  # 사용법 오류가 차단(exit 2)으로 새지 않게 한다
        return 0 if exc.code == 0 else 1
    if guard_args and guard_args[0] == "--":
        guard_args = guard_args[1:]

    payload = sys.stdin.buffer.read()
    guard = Path(args.guard).expanduser()
    if not guard.is_file():
        print(f"rejectbench.wrapper: 가드 스크립트가 없다: {args.guard}", file=sys.stderr)
        return 1
    try:
        proc = run_guard(guard, guard_args, payload)
    except Exception as exc:
        print(
            f"rejectbench.wrapper: 가드 실행 실패({type(exc).__name__}): {args.guard}",
            file=sys.stderr,
        )
        return 1

    # 투명 전달 — 기록 시도보다 먼저, 바이트 그대로.
    sys.stdout.buffer.write(proc.stdout)
    sys.stdout.buffer.flush()
    sys.stderr.buffer.write(proc.stderr)
    sys.stderr.buffer.flush()

    try:
        record_guard_result(
            payload_text=payload.decode("utf-8", errors="replace"),
            guard_path=str(guard),
            result=GuardResult(
                exit_code=proc.returncode,
                stdout=proc.stdout.decode("utf-8", errors="replace"),
                stderr=proc.stderr.decode("utf-8", errors="replace"),
            ),
            harness=args.harness,
        )
    except Exception:
        # record_guard_result 자체가 던지지 않는 계약이지만, 이 자리에서마저
        # 예외가 가드 결과를 바꾸는 일은 없어야 한다.
        pass
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
