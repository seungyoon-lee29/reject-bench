#!/usr/bin/env python3
"""Reject Bench 수집기 v0 — Claude Code 훅 이벤트를 메타데이터만 JSONL로 적재한다.

설계 원칙 세 가지.

1. **원문은 저장하지 않는다.** 파일 내용·Bash 명령어 원문·프롬프트는 기록 대상이 아니다.
   기록하는 것은 "무엇이 일어났는가"의 골격뿐이다(도구명, 경로, 성공/실패, 사유 힌트).
   전역 규칙 "비밀은 어디에도 평문 금지"의 하한을 훅 단계에서 지킨다.
2. **절대 세션을 깨지 않는다.** 어떤 예외가 나도 exit 0. 수집 실패는 로그 한 줄을 잃는 것이고,
   세션을 멈추는 건 그보다 훨씬 비싸다.
3. **스키마 확정을 기다리지 않는다.** 알 수 없는 필드는 버리고 아는 것만 남긴다.
   나중에 스키마가 바뀌어도 `schema` 필드로 구분해서 마이그레이션한다.

사용: collect.py <EVENT_NAME>   (stdin: 훅 입력 JSON)
"""

import json
import os
import re
import sys
from datetime import datetime, timezone

SCHEMA = "v0"
OUT = os.path.expanduser("~/workspace/reject-bench/data/events.jsonl")

# 토큰처럼 생긴 긴 문자열은 마스킹한다. 에러 메시지에 자격증명이 섞여 들어오는 경로를 차단.
TOKENish = re.compile(r"[A-Za-z0-9_\-./+:@]{24,}")
ERR_MAX = 200


def redact(text):
    """긴 토큰류를 마스킹하고 길이를 자른다. None/비문자열은 None."""
    if not isinstance(text, str) or not text:
        return None
    masked = TOKENish.sub("<redacted>", text)
    return masked[:ERR_MAX]


def first_token(command):
    """Bash 명령어에서 실행 파일 이름만 남긴다(예: 'npm', 'pytest', 'git').

    분류에는 충분하고, 인자에 섞일 수 있는 시크릿은 애초에 읽지 않는다.
    """
    if not isinstance(command, str):
        return None
    for part in command.strip().split():
        if "=" in part:  # FOO=bar 형태의 환경변수 prefix는 건너뛴다
            continue
        return os.path.basename(part)[:40]
    return None


def rel_path(path):
    """홈 디렉터리를 ~ 로 축약. 경로는 어느 프로젝트의 어떤 파일인지 분류에 필요하다."""
    if not isinstance(path, str) or not path:
        return None
    home = os.path.expanduser("~")
    return ("~" + path[len(home):]) if path.startswith(home) else path


def main():
    event = sys.argv[1] if len(sys.argv) > 1 else "unknown"

    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    tool_input = payload.get("tool_input") or {}
    tool_response = payload.get("tool_response") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}
    if not isinstance(tool_response, dict):
        tool_response = {}

    cwd = payload.get("cwd") or os.getcwd()

    record = {
        "schema": SCHEMA,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event": event,
        "session_id": payload.get("session_id"),
        # 프로젝트 단위 집계용. 전체 경로가 아니라 폴더명만.
        "project": os.path.basename(cwd) if isinstance(cwd, str) else None,
        "tool": payload.get("tool_name"),
        "path": rel_path(tool_input.get("file_path") or tool_input.get("notebook_path")),
        "cmd": first_token(tool_input.get("command")),
        # 변경 규모: 내용이 아니라 크기만.
        "size": len(tool_input["content"]) if isinstance(tool_input.get("content"), str) else None,
        "edits": len(tool_input["edits"]) if isinstance(tool_input.get("edits"), list) else None,
        "error": redact(
            payload.get("error")
            or payload.get("reason")
            or tool_response.get("error")
            or (tool_response.get("stderr") if isinstance(tool_response.get("stderr"), str) else None)
        ),
        # 기각 사유 태그. 수집 시점에는 비워두고 사후에 사람이 채운다(자동 분류는 v1에서).
        "tag": None,
    }

    record = {k: v for k, v in record.items() if v is not None}

    try:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        with open(OUT, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass  # 원칙 2: 수집 실패가 세션을 막지 않는다

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
