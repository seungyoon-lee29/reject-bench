"""정규화 직렬화 해시.

`content_hash`의 해시 도메인은 GuardSpec 의미 5필드(`purpose`, `policy`,
`exceptions`, `allow_examples`, `block_examples`)로 한정된다. `guard_id`·
`version`·`project`·`created_at` 같은 식별 필드는 도메인 밖이므로, 내용이
같으면 언제 어떤 이름으로 등록해도 해시가 같다 — 버전 강제를 내용 비교로
구현하기 위한 계약이다 (spec §3.1).

정규화 규칙:
- 모든 문자열은 유니코드 NFC로 정규화한다.
- JSON 직렬화는 키 정렬, 최소 구분자, ensure_ascii=False로 고정한다.
- 목록 순서는 의미의 일부다 (예시 순서가 다르면 다른 내용이다).
"""

from __future__ import annotations

import hashlib
import json
import unicodedata

_HASH_PREFIX = "sha256:"


def _normalize(value):
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    if isinstance(value, dict):
        return {unicodedata.normalize("NFC", k): _normalize(v) for k, v in value.items()}
    return value


def canonical_json(value) -> str:
    """정규화된 결정적 JSON 직렬화."""
    return json.dumps(
        _normalize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def _sha256(text: str) -> str:
    return _HASH_PREFIX + hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_hash(
    *,
    purpose: str,
    policy: str,
    exceptions: tuple[str, ...],
    allow_examples: tuple[str, ...],
    block_examples: tuple[str, ...],
) -> str:
    """GuardSpec 의미 5필드만의 정규화 해시. 다른 필드는 받지 않는다."""
    payload = {
        "purpose": purpose,
        "policy": policy,
        "exceptions": list(exceptions),
        "allow_examples": list(allow_examples),
        "block_examples": list(block_examples),
    }
    return _sha256(canonical_json(payload))


def value_hash(value) -> str:
    """amendment의 이전 값 해시 등 단일 값의 정규화 해시."""
    return _sha256(canonical_json(value))
