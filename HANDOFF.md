# 핸드오프 — 003 기준선 측정 대기 (2026-09-01)

## 지금 하던 것

003 주기(운영 경험 개선 증분)의 계약 3종을 적대 리뷰 결정대로 **개정 완료**했다. `.dryforge/{spec,plan,handoff}.md`가 이제 정본이다.

- 태스크가 5개 → **선행 절차 하나 + 8개(E0~E7)**로 늘었다. 기준선 측정이 빌드 앞으로 왔고, E0(전역 배선 수정)·E6(정본 갱신)·E7(보고서 파티션)이 신설됐다.
- 개정 중 미결 3건을 확정했다 — E1 되물림 하한 = **본문 상한의 50%**(비율), E2 필드 = **`session_id_format` / `conforming`·`nonconforming`·`unchecked`**, E7 파티션 범위 = **`operation_event_ids` 파생 지표 전부**(근거는 `.dryforge/spec.md` §3.3·§4.4·§9).
- 근거 문서: [`docs/운영경험개선-증분-적대리뷰.md`](docs/운영경험개선-증분-적대리뷰.md)(finding 27건), [`docs/운영경험개선-증분-결정기록.md`](docs/운영경험개선-증분-결정기록.md)(결정 10건 + 16건 처분). **둘 다 정본이 아니다** — "왜 그렇게 정했는가"만 보존하며 줄 번호는 개정 전(HEAD `9548ae0`) 기준이다.
- 리뷰 하네스는 스킬로 뽑아 공개: `.claude/skills/adversarial-review-blind/` → https://github.com/seungyoon-lee29/adversarial-review-blind

커밋 0(`.dryforge/` 3종 + `docs/` 2종 + 이 파일 + 스킬)은 `.dryforge/plan.md`의 "커밋 경계"에 정의돼 있다.

## 다음 할 일 (구체적 첫 행동 1개)

**기준선 측정을 수행한다** — `docs/관찰-프로토콜.md`의 기준선 절차를 열고, 기록된 `block-dangerous-git` 자기차단 2건 중 첫 건의 transcript+git 복원부터 시작한다.

빌드가 아니라 절차이며 plan 태스크가 아니다. **측정을 마치기 전에는 E0 이하 어느 태스크도 시작하지 않는다.** 측정이 끝나면 `CLAUDE.md`/`AGENTS.md` 비협상 ⑦의 조회 금지가 해제되고, 그때 E3 보장이 현 데이터에 공집합인지 확인할 수 있다. 결과는 이 파일에 남긴다.

그 뒤 E0 → E1 → … → E7. 각 태스크의 체크박스는 `.dryforge/plan.md`에 있다.

## 미결 결정

1. **검토 큐 2건** — `block-dangerous-git` 자기차단 2건. 기준선 측정 대상이며, 측정 뒤 `review demote --reason` 또는 유용성 판정을 정한다.
2. **판정 첫 과금 호출** — 사용자 승인은 기록됨(`.dryforge/handoff.md` "문서에 담기지 않은 저작 시점 의도" 첫 항목). 실행은 기준선·큐 처리 시점에 `--approve-billing`과 함께.
3. **MCP 서버 전역 등록 확장** — 기준선 측정 뒤에만 판단.
4. **외부 검증(X1)** — O2 뒤에만.

## 함정

- **기준선 측정 전에는 `guard_evidence`·`get_report`를 호출하지 않는다.** 등록 확인 기동과 `list_guards`만 허용. 도구 보고서를 먼저 읽으면 `docs/관찰-프로토콜.md`가 요구하는 transcript+git 복원이 오염된다. 측정을 마치면 해제된다.
- **전역 가드 자기차단이 곧 운영 사건이다.** 위험 패턴 텍스트를 heredoc/echo로 쓰면 전역 가드가 명령 전문 매칭으로 차단하고 그 발동이 실제 `operation` 사건으로 기록된다. 파일 도구(Write/Edit)로 쓸 것. 시험·강제 발동은 `REJECTBENCH_TEST_SESSION` 아래에서만.
- **E1·E2는 라이브 훅 경로의 파일을 고친다.** `pyproject.toml:15`가 `package = false`라 훅은 작업 트리 코드를 해석한다. E0 이후엔 이게 **모든 저장소**에 적용되므로 편집 후 `import rejectbench.wrapper` 스모크를 매번 돌린다.
- **E0을 E4보다 먼저.** v0 `collect.py`는 직접 경로라 지금 모든 저장소에서 작동하고 v7 래퍼만 무동작이다(저장소 밖에서 `ModuleNotFoundError`). 순서를 뒤집으면 작동하는 관측자를 먼저 걷어내는 구간이 생긴다.
- **전역 설정(`~/.claude/settings.json`) 편집은 스냅샷·롤백을 거친다** — E0·E4 둘 다. git 밖이라 되돌릴 다른 수단이 없다(`.dryforge/handoff.md` 하드 게이트 5).
- **테스트는 임시 store만.** 운영 `data/`에 닿으면 conftest autouse 게이트가 하드 실패시킨다. E3 실기동은 `--store <임시 경로>` 인자로 별도 기동 — 완성된 명령 한 줄은 `.dryforge/handoff.md` 하드 게이트 3에 있다. `.mcp.json` 등록 서버·인자 없는 기동은 조용히 운영 store를 읽는다.
- **`.claude/settings.json.bak-1786598695`, `.codex/config.toml.bak-1786598644`는 사용자 소유 비추적 백업.** 스테이징·수정·삭제 금지. E0·E4가 만들 새 스냅샷은 다른 이름을 써야 한다.
- `.dryforge/`의 완료 주기는 번호 폴더(`001/`, `002/`), 진행 중 주기만 루트. 하드 게이트 9에 따라 루트 3종은 git 추적 대상이다.
