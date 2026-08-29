"""적재 시점 비밀 제거 (spec §4, §3.2).

운영 저장소에는 적재 시점부터 비밀 평문이 존재해서는 안 된다. 일반적
자격증명 형태를 플레이스홀더로 치환하되, 정상 문장·파일 경로·짧은 해시
표기는 훼손하지 않는다 — 그 경계는 양성 대조 테스트(`tests/test_scrub.py`)가
고정한다.

패턴 선택 기준:
- 서비스 접두 키(sk-, ghp_, github_pat_, AKIA/ASIA, xox?-), Bearer 값,
  JWT, `*_KEY=`/`*_TOKEN=` 류 대입값은 형태만으로 자격증명으로 본다.
- 긴 무작위 연속열은 보수적으로 잡는다: hex는 48자 이상(전체 git SHA-1
  40자는 통과), base64형은 40자 이상이면서 대문자·소문자·숫자를 모두
  섞은 경우만. 경로 구분자 `/`에 붙은 연속열은 경로 조각으로 보고 남긴다.
- `sha256:` 표기가 붙은 해시는 이 도구 자신의 해시 표기이므로 남긴다.

이 모듈은 순수 함수만 담는다 — 파일도 환경도 읽지 않는다.
"""

from __future__ import annotations

import re

PLACEHOLDER = "[REDACTED]"

# 이름은 남기고 값만 지운다 — `NAME=값` 형태의 자격증명 대입.
_ENV_ASSIGN = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?))"
    r"=(\"[^\"\n]*\"|'[^'\n]*'|[^\s'\"]+)",
    re.IGNORECASE,
)

# `Bearer <값>` — 스킴 단어는 남긴다.
_BEARER = re.compile(r"\b([Bb]earer)\s+[A-Za-z0-9._~+/=-]{8,}")

# 형태만으로 자격증명인 접두 키·JWT.
_TOKEN_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{16,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]*"),
)

# 긴 hex 연속열. 48자 미만(축약 해시·git SHA-1 40자)과 `sha256:` 표기는 남긴다.
_LONG_HEX = re.compile(r"(?<!sha256:)\b[0-9a-fA-F]{48,}\b")

# base64형 연속열 후보. `/` 인접 연속열은 경로 조각으로 보고 제외한다.
_BASE64_RUN = re.compile(r"(?<![A-Za-z0-9+/=\[])[A-Za-z0-9+]{40,}={0,2}(?![A-Za-z0-9+/=])")


def _base64_replacement(match: re.Match[str]) -> str:
    run = match.group(0)
    has_lower = any(c.islower() for c in run)
    has_upper = any(c.isupper() for c in run)
    has_digit = any(c.isdigit() for c in run)
    return PLACEHOLDER if (has_lower and has_upper and has_digit) else run


def scrub_text(text: str) -> str:
    """일반적 자격증명 형태를 플레이스홀더로 치환한다. 멱등이다."""
    out = _ENV_ASSIGN.sub(lambda m: f"{m.group(1)}={PLACEHOLDER}", text)
    out = _BEARER.sub(lambda m: f"{m.group(1)} {PLACEHOLDER}", out)
    for pattern in _TOKEN_PATTERNS:
        out = pattern.sub(PLACEHOLDER, out)
    out = _LONG_HEX.sub(PLACEHOLDER, out)
    return _BASE64_RUN.sub(_base64_replacement, out)
