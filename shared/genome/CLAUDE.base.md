# CLAUDE.base.md

> **게놈 헌법 base (Tier B) — v0.1.0.** 자식 워크플로우는 자기 CLAUDE.md/AGENTS.md/soul.md 최상단에서
> 이 파일을 `@import`하고, 그 아래 **도메인 overlay** 섹션만 추가한다. 이 파일을 제자리 수정하지 말 것
> (게놈 버전드 — 업그레이드는 P4 게이트를 통한다). 도메인 특수화는 overlay와 `genome.config.yaml`로 한다.

---

Claude Code 기반의 에이전트 워크플로우 자동화 시스템.

## 최종 목표

1. **워크플로우 설계**: 복잡한 작업을 Research → Planning → Implementation 3단계 구조의 `workflow.md`로 설계
2. **워크플로우 실행**: `workflow.md`에 정의된 에이전트·스크립트·자동화 구성을 **실제로 구현**

> 워크플로우를 만드는 것은 중간 산출물이다. **워크플로우에 기술된 내용이 실제로 동작하는 것**이 최종 목표다.

### 존재 이유 — DNA 유전

이 시스템은 부모 유기체의 게놈을 상속한 자식이며, 어떤 도메인의 자식이든 부모의 전체 게놈(헌법·구조·검증·안전·기억·비판·투명)을 **내장**한다. 상세: `soul.md §0`.

> **자식 overlay 지점**: 이 자식 시스템의 구체적 도메인 목표·정체성은 overlay 섹션에서 선언한다.

## 절대 기준

> 모든 설계·구현·수정 의사결정에 적용되는 최상위 규칙. 아래 모든 원칙보다 상위.

### 절대 기준 1: 최종 결과물의 품질
> **속도, 토큰 비용, 작업량, 분량 제한은 완전히 무시한다.** 유일한 기준은 **최종 결과물의 품질**이다.

### 절대 기준 2: 단일 파일 SOT + 계층적 메모리 구조
> 모든 공유 상태는 단일 파일(SOT)에 집중. SOT 쓰기는 Orchestrator/Team Lead만. 병렬 에이전트의 동일 파일 동시 수정 금지.

### 절대 기준 3: 코드 변경 프로토콜 (CCP)
> 코드를 작성·수정·추가·삭제하기 전에 **Step 1(의도 파악) → Step 2(영향 범위 분석) → Step 3(변경 설계)**를 내부적으로 수행. 분석 깊이는 변경 규모에 비례. **상세**: `docs/protocols/code-change-protocol.md`

**코딩 기준점 (CAP)**: CAP-1(코딩 전 사고), CAP-2(단순성 우선), CAP-3(목표 기반 실행), CAP-4(외과적 변경). 절대 기준 1과 충돌 시 품질이 우선.

### 절대 기준 간 우선순위
> **절대 기준 1(품질)이 최상위**. 절대 기준 2(SOT)와 3(CCP)은 품질을 보장하기 위한 동위 수단.

---

## Context Preservation System

컨텍스트 토큰 초과·`/clear`·압축 시 작업 내역 상실을 방지하는 자동 저장·복원 시스템.

| Hook 이벤트 | 스크립트 | 동작 |
|------------|---------|------|
| Setup (`--init`) | `setup_init.py` | 인프라 건강 검증 + SOT 쓰기 안전 + 런타임 디렉터리 생성 |
| Setup (`--maintenance`) | `setup_maintenance.py` | 주기적 건강 검진 + doc-code 동기화 |
| PreToolUse (Bash) | `block_destructive_commands.py` | 위험 명령 차단 — 네트워크 유출+시스템 파괴+Git 파괴+치명적 rm (exit 2) |
| PreToolUse (Edit\|Write) | `block_test_file_edit.py` | TDD 모드 시 테스트 파일 보호 (exit 2) |
| PreToolUse (Edit\|Write) | `predictive_debug_guard.py` | 에러 이력 기반 위험 파일 경고 |
| SessionStart | `restore_context.py` | RLM 포인터 + 과거 세션 인덱스 + Predictive Debugging 캐시 |
| PostToolUse (9개 도구) | `update_work_log.py` | 작업 로그 누적 |
| PostToolUse (Bash\|Read) | `output_secret_filter.py` | 시크릿 탐지 (3-tier 추출, 25+ 패턴, 2-패스 스캔) |
| PostToolUse (Edit\|Write) | `security_sensitive_file_guard.py` | 보안 민감 파일 수정 경고 |
| Stop | `generate_context_summary.py` | 증분 스냅샷 + Knowledge Archive + 안전망 |
| PreCompact | `save_context.py` | 압축 전 스냅샷 저장 |
| SessionEnd | `save_context.py` | `/clear` 시 전체 스냅샷 저장 |

**필수 행동**: 세션 시작 시 `[CONTEXT RECOVERY]` 표시되면, 안내된 파일을 **반드시 Read tool로 읽어** 이전 맥락을 복원.

**상세**: Hook 내부 메커니즘, Knowledge Archive 필드, D-7 인스턴스 → `docs/protocols/context-preservation-detail.md`

## 스킬 사용 판별

| 사용자 요청 패턴 | 스킬 | 진입점 |
|----------------|------|--------|
| "워크플로우 만들어줘", "자동화 파이프라인 설계" | `workflow-generator` | SKILL.md |

> **자식 overlay 지점**: 도메인 전용 스킬(예: 학술 글쓰기, 도메인 스캔 등)은 overlay 테이블에 추가한다.

## 설계 원칙

1. **P1 — 정확도를 위한 데이터 정제**: AI 전달 전 Python 등으로 노이즈 제거
2. **P2 — 전문성 기반 위임 구조**: 전문 에이전트에게 위임, Orchestrator는 조율만
3. **P3 — 이미지/리소스 정확성**: 정확한 다운로드 경로 명시, placeholder 불가
4. **P4 — 질문 설계 규칙**: 최대 4개 질문, 각 3개 선택지. 모호함 없으면 질문 없이 진행

## Autopilot Mode

워크플로우 실행 시 `(human)` 단계와 AskUserQuestion을 자동 승인하는 모드. 상세: `AGENTS.md §5.1`

**4계층 품질 보장**: L0(Anti-Skip Guard) → L1(Verification Gate) → L1.5(pACS Self-Rating) → L2(Calibration). 상세: `docs/protocols/quality-gates.md`

**워크플로우 실행 전 반드시 읽기**: `docs/protocols/autopilot-execution.md` — 단계별 체크리스트 + NEVER DO

## ULW (Ultrawork) Mode

프롬프트에 `ulw` 포함 시 활성화되는 **철저함 강도 오버레이**. Autopilot(자동화 축)과 직교. 3가지 강화 규칙: I-1(Sisyphus Persistence), I-2(Mandatory Task Decomposition), I-3(Bounded Retry Escalation).

**상세**: `docs/protocols/ulw-mode.md`

## 언어 및 스타일 규칙

- **프레임워크 문서·사용자 대화**: 한국어
- **워크플로우 실행**: 영어 (AI 성능 극대화 — 절대 기준 1 근거)
- **최종 산출물**: 영어 원본 + 한국어 번역 쌍 (`@translator` 서브에이전트) — *번역 leg는 산출물이 영한 쌍인 자식만 활성. `genome.config.yaml`의 `translation` 항목 참조*
- **기술 용어**: 영어 유지 (SOT, Agent Team, Hooks 등)
- **시각화**: Mermaid 다이어그램 선호
- **깊이**: 간략 요약보다 포괄적·데이터 기반 서술 선호

> **자식 overlay 지점**: 산출물 언어 정책(예: 단일 언어 산출, 공공 전달 문체 등)이 base와 다르면 overlay에서 명시한다.

### 번역 프로토콜

워크플로우에 `Translation: @translator`로 표기된 단계에 한해 `@translator` 서브에이전트 호출. 번역 대상은 텍스트 콘텐츠(`.md`, `.txt`)만. SOT `outputs.step-N-ko`에 기록. 용어 사전 `translations/glossary.yaml` 자동 유지.

> **경계 케이스(번역 leg 활성/비활성)**: 번역 프로토콜은 *개념*으로 base에 둔다. 실제 활성 여부는 자식이 `genome.config.yaml`로 결정한다 — 산출물이 영한 쌍인 자식(예: 콘텐츠/리서치 파이프라인)만 켜고, 도메인 언어가 단일하거나 코드 산출 위주인 자식은 끈다. `genome.config: translation` 참조.

## 스킬 개발 규칙

1. **모든 절대 기준을 반드시 포함** — 해당 도메인에 맞게 맥락화
2. **파일 간 역할 분담** 명확히 — SKILL.md(WHY), references/(WHAT/HOW/VERIFY)
3. **절대 기준 간 충돌 시나리오** 구체적으로 명시
4. 수정 후 반드시 **절대 기준 관점에서 성찰**
