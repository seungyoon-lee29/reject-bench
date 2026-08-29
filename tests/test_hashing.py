"""content_hash 계약: 해시 도메인 = 의미 5필드, 정규화 직렬화."""

from __future__ import annotations

import re
import unicodedata

from rejectbench import content_hash
from tests.factories import make_spec, ts

BASE = dict(
    purpose="p",
    policy="block force push",
    exceptions=("rescue branch",),
    allow_examples=("git status",),
    block_examples=("git push --force",),
)


def test_hash_format():
    h = content_hash(**BASE)
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", h)


def test_same_content_same_hash_regardless_of_identity_fields():
    a = make_spec(guard_id="guard-a", version=1, project="reject-bench", created_at=ts(0))
    b = make_spec(
        guard_id="guard-b",
        version=7,
        spec_id="spec-other",
        project="global",
        created_at=ts(999),
    )
    # guard_id·version·project·created_at·spec_id는 해시 도메인 밖이다.
    assert a.content_hash == b.content_hash


def test_each_semantic_field_is_in_the_hash_domain():
    base = content_hash(**BASE)
    variants = [
        {**BASE, "purpose": "다른 목적"},
        {**BASE, "policy": "다른 정책"},
        {**BASE, "exceptions": ()},
        {**BASE, "allow_examples": ("git log",)},
        {**BASE, "block_examples": ("git reset --hard",)},
    ]
    hashes = [content_hash(**v) for v in variants]
    assert all(h != base for h in hashes)
    assert len(set(hashes)) == len(hashes)


def test_unicode_normalization_makes_equivalent_text_hash_equal():
    nfc = unicodedata.normalize("NFC", "café 가드")
    nfd = unicodedata.normalize("NFD", "café 가드")
    assert nfc != nfd  # 전제: 표현이 실제로 다르다
    assert content_hash(**{**BASE, "policy": nfc}) == content_hash(**{**BASE, "policy": nfd})


def test_example_order_is_semantic():
    a = content_hash(**{**BASE, "block_examples": ("x", "y")})
    b = content_hash(**{**BASE, "block_examples": ("y", "x")})
    assert a != b
