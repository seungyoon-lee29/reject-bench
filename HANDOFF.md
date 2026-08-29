# 핸드오프 — v7 구현 주기(T1~T6) 완료·관측창 오픈, 통합 결정 대기 (2026-08-29)

## 지금 하던 것

dryforge go로 T1~T6을 브랜치 `dryforge/v7-build`에 구현 완료했다(main 미변경). 태스크별 red-green + 독립 스펙 검수(T1~T3) + 완료 게이트 `uv run pytest` 357건 exit 0 (`c06ed81`). 관측창은 열려 있다: 관찰 프로토콜 선등록(`docs/관찰-프로토콜.md` — 종료 조건 4주 또는 판정 가능 가드 1개 성립, Codex 범위 밖), 실존 가드 2종 v1 등록(`data/v7`), 배선 설치(`docs/배선-목록.md` — 전역·reply-gate settings.json이 래퍼 경유, 백업 생성됨). 문서층은 기존 관장 체계 유지 결정 — 추가는 `rejectbench/AGENTS.md` 신설과 CLAUDE/AGENTS/README 최소 추기뿐.

## 다음 할 일 (구체적 첫 행동 1개)

**최종 리뷰 통과 후 통합 결정**: `dryforge/v7-build`를 main에 머지할지(PR/직접/보류) 사용자가 정한다. 통합 후에야 reply-gate 배포 잔여 작업(1차 관측 무대) 착수.

## 미결 결정

1. **검토 큐 2건 처리** — 구현 중 전역 가드가 에이전트 명령을 차단해 `operation` 사건 2건이 기록됨(`ev-e767…`, `ev-94f0…`). `review list`로 보고 test 강등이든 유용성 판정이든 사용자가 결정
2. **v0 `hooks/collect.py` 퇴역** — 배선 병행 상태 확인 후 별도 커밋(`docs/배선-목록.md` cutover 절)
3. **판정 첫 과금 호출** — 사건이 쌓인 뒤 `judge --approve-billing` 시점에 승인(기본 dry-run). 모델 기본 gpt-5-mini, temperature 거부 시 settings 조정
4. **외부 검증(X1)** — O2 뒤에만

## 함정

- 시험·강제 발동은 반드시 `REJECTBENCH_TEST_SESSION` 아래에서 — 플래그 없는 발동은 `operation`으로 기록된다 (배선이 살아 있다!)
- 전역 가드는 명령 문자열 전문 매칭 — 위험 패턴 텍스트를 heredoc으로 쓰면 자기 차단된다. 파일 도구로 쓸 것 (`rejectbench/AGENTS.md` 함정 절)
- 배선 명령은 `uv run --project …` 필수 — 맨 `python3 -m`은 패키지를 못 찾는다
- reply-gate `.claude/settings.json` 변경분은 그 저장소에서 비커밋 — 커밋은 사용자 소관 (백업 `settings.json.bak-1787966866`)
- 산입·post-remove·신규 가드 표기는 파생 계산이 정본 — 저장값 신뢰 금지 (`rejectbench/AGENTS.md`)
- 판정 교정 레코드는 사이드카 `calibration.jsonl`, 기준선은 `baseline.json` 관례
- 관찰 프로토콜 변경은 사유 있는 추기로만. 실행 상태는 plan+HANDOFF만 기록
- `.claude/settings.json.bak-*`, `.codex/config.toml.bak-*`는 사용자 소유 비추적 백업 — 건드리지 않는다
- `docs/문제정의/01~05`·심사 스킬은 보존 결정(3중 정본 + 2026-08-28 재확인) — 폴더 정리로 지우지 않는다
