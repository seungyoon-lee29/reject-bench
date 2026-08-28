"""가드 등록부 — 버전 고정 GuardSpec 맥락의 등록·조회 (spec §3.1, §7).

버전 강제는 내용 비교로 구현한다: 같은 guard_id의 최신 버전과
`content_hash`(T1 hashing, 의미 5필드 한정)가 같으면 무해 멱등으로
아무것도 쓰지 않고, 다르면 반드시 version+1로 새 spec을 append한다.
기존 버전의 재사용·덮어쓰기 경로는 없다 — 저장소에 같은 (guard_id,
version)의 상충 내용이 발견되면 로드 자체를 거부한다.

`enforcement_ref`는 정책의 대체물이 아니라 drift 감지용 메타데이터다.
가드 스크립트 파일은 해시 계산을 위해 바이트로 읽기만 하고 절대
실행하지 않는다.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from rejectbench.records import EnforcementRef, GuardSpec
from rejectbench.store import AppendStore


class RegistryError(Exception):
    """등록부 규칙 위반."""


class QualityError(RegistryError):
    """예시·예외 최소 품질 위반 — policy 공백, 예시 누락 등."""


class VersionConflictError(RegistryError):
    """같은 (guard_id, version)에 상충하는 내용 — 덮어쓰기 시도의 흔적."""


class UnknownSpecError(RegistryError):
    """존재하지 않는 guard_id 또는 version."""


class SpecReferenceError(RegistryError):
    """존재하지 않거나 사건보다 늦은 spec 참조."""


class EnforcementScriptError(RegistryError):
    """가드 스크립트 파일을 읽을 수 없다."""


@dataclass(frozen=True)
class RegisterResult:
    spec: GuardSpec
    created: bool  # False → 같은 내용 재등록의 무해 멱등


def enforcement_ref_for(script_path: str | Path) -> EnforcementRef:
    """가드 스크립트의 경로·SHA-256 참조. 바이트로 읽기만 한다 — 실행 금지."""
    path = Path(script_path).expanduser()
    if not path.is_file():
        raise EnforcementScriptError(f"가드 스크립트 파일이 없다: {script_path}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return EnforcementRef(script_path=str(script_path), file_hash=f"sha256:{digest}")


def check_quality(
    *,
    policy: str,
    exceptions: tuple[str, ...],
    allow_examples: tuple[str, ...],
    block_examples: tuple[str, ...],
) -> None:
    if not policy.strip():
        raise QualityError("policy: 비어 있지 않아야 한다")
    if len(allow_examples) < 1:
        raise QualityError("allow_examples: 최소 1건이 필요하다")
    if len(block_examples) < 1:
        raise QualityError("block_examples: 최소 1건이 필요하다")
    for name, values in (
        ("exceptions", exceptions),
        ("allow_examples", allow_examples),
        ("block_examples", block_examples),
    ):
        for value in values:
            if not value.strip():
                raise QualityError(f"{name}: 공백 항목을 허용하지 않는다")


class GuardRegistry:
    """AppendStore 위의 등록부 뷰. append와 조회만 있고 수정·삭제는 없다."""

    def __init__(self, store: AppendStore):
        self._store = store
        self._specs: dict[tuple[str, int], GuardSpec] = {}
        for record in store.load().records:
            if not isinstance(record, GuardSpec):
                continue
            key = (record.guard_id, record.version)
            existing = self._specs.get(key)
            if existing is None:
                self._specs[key] = record
            elif existing.content_hash != record.content_hash:
                raise VersionConflictError(
                    f"{record.guard_id} v{record.version}: 같은 버전에 상충하는 "
                    "내용이 저장돼 있다 — 기존 버전 덮어쓰기는 금지다"
                )
            # 동일 내용의 중복 줄은 무해하다 — 최초 레코드를 유지한다.

    # --- 조회 ----------------------------------------------------------------

    def guard_ids(self) -> list[str]:
        return sorted({guard_id for guard_id, _ in self._specs})

    def versions(self, guard_id: str) -> list[GuardSpec]:
        specs = sorted(
            (spec for (gid, _), spec in self._specs.items() if gid == guard_id),
            key=lambda spec: spec.version,
        )
        if not specs:
            raise UnknownSpecError(f"등록되지 않은 가드: {guard_id}")
        return specs

    def get(self, guard_id: str, version: int) -> GuardSpec:
        spec = self._specs.get((guard_id, version))
        if spec is None:
            raise UnknownSpecError(f"등록되지 않은 spec: {guard_id} v{version}")
        return spec

    def latest(self, guard_id: str) -> GuardSpec:
        return self.versions(guard_id)[-1]

    def resolve_reference(
        self, guard_id: str, version: int, *, occurred_at: datetime
    ) -> GuardSpec:
        """사건 참조 해석 — 존재하지 않거나 사건보다 늦은 spec은 차단한다.

        append 순서 근거의 선행성 검사는 T1 `Dataset.check_integrity`가
        담당하고, 이 조회는 등록부 수준에서 같은 계약을 이어받는다.
        """
        try:
            spec = self.get(guard_id, version)
        except UnknownSpecError as exc:
            raise SpecReferenceError(str(exc)) from exc
        if spec.created_at > occurred_at:
            raise SpecReferenceError(
                f"{guard_id} v{version}: spec 생성 시각이 사건보다 늦다 "
                f"({spec.created_at.isoformat()} > {occurred_at.isoformat()})"
            )
        return spec

    # --- 등록 ----------------------------------------------------------------

    def register(
        self,
        *,
        guard_id: str,
        project: str,
        purpose: str,
        policy: str,
        exceptions: tuple[str, ...] = (),
        allow_examples: tuple[str, ...],
        block_examples: tuple[str, ...],
        enforcement_ref: EnforcementRef | None = None,
        created_at: datetime | None = None,
    ) -> RegisterResult:
        """내용 비교로 버전을 강제하는 유일한 쓰기 경로.

        - 새 guard_id → v1
        - 최신 버전과 같은 content_hash → 무해 멱등 (append 없음)
        - 최신 버전과 다른 content_hash → 반드시 최신 버전+1
        """
        exceptions = tuple(exceptions)
        allow_examples = tuple(allow_examples)
        block_examples = tuple(block_examples)
        check_quality(
            policy=policy,
            exceptions=exceptions,
            allow_examples=allow_examples,
            block_examples=block_examples,
        )
        latest: GuardSpec | None = None
        try:
            latest = self.latest(guard_id)
        except UnknownSpecError:
            pass
        version = 1 if latest is None else latest.version + 1
        spec = GuardSpec.create(
            spec_id=f"spec-{guard_id}-v{version}",
            guard_id=guard_id,
            version=version,
            project=project,
            purpose=purpose,
            policy=policy,
            exceptions=exceptions,
            allow_examples=allow_examples,
            block_examples=block_examples,
            created_at=created_at or datetime.now(timezone.utc),
            enforcement_ref=enforcement_ref,
        )
        if latest is not None and latest.content_hash == spec.content_hash:
            return RegisterResult(spec=latest, created=False)
        self._store.append(spec)
        self._specs[(guard_id, version)] = spec
        return RegisterResult(spec=spec, created=True)
