# 봇/자동화 규칙 (L1)

> **L0 절대규칙 필수 준수** — 아래 규칙은 L0에서 발췌한 것이며, 이 레포의 모든 작업에 강제 적용된다.
> 전문: ~/CLAUDE.md 또는 workspace/CLAUDE_L0.md

## L0 강제 규칙 (발췌 — 생략/완화 금지)

### 3-에이전트 독립 검증 (필수)

데이터 수집/리서치/수치 산출 작업 시 반드시 3개 에이전트를 **서로 다른 방법론**으로 병렬 실행한다.

| 에이전트 | 역할 |
| ------ | ------ |
| Agent A | 공식 API/원문에서 직접 추출 |
| Agent B | 다른 경로/방법론으로 독립 추출 |
| Agent C | WebSearch 등 제3자 소스로 교차검증 |

- 3개 모두 일치 → 채택. 불일치 → 사용자 판단 요청
- 불일치를 임의로 평균/다수결 처리 **절대 금지**

### 레드팀 검증 (필수)

- 결과물 완성 후 **독립 서브에이전트**로 레드팀 검증 실행
- 검증 대상: 수치 정합성, 출처 유효성, 논리적 일관성
- 레드팀 없이 최종 산출물을 사용자에게 전달하는 것은 **L0 위반**

### 정확성

- 모르면 WebSearch. 그래도 모르면 "모른다"
- **뇌피셜 금지**. 추정으로 답하지 않는다
- 수치에는 반드시 출처 명시

### 오류 보고

- 변명 금지. 구조적 원인 분석 → 재발 방지 보고

---

## 역할

- 텔레그램 봇 자동화 시스템의 개발/유지보수
- 운동 코칭 데이터 처리 및 분석
- GitHub Actions 워크플로우 관리

## 봇 구성

| 봇 | username | GitHub Secret | 용도 |
| ------ | ------ | ------ | ------ |
| SY Workspace | @SY_workspace_bot | TELEGRAM_BOT_TOKEN | 할일, DART, watchlist |
| SY IB News | @SY_IB_News_bot | IB_BOT_TOKEN | IB 뉴스 클리핑 |
| SY Training | @SY_workout_bot | TRAINING_BOT_TOKEN | 운동, 철인3종, 운동 분석 |
| SY Real Estate | @SY_realestate_bot | REALESTATE_BOT_TOKEN | 부동산 주간 리포트 |

- Chat ID: `TELEGRAM_CHAT_ID` (모든 봇 공통)
- GitHub repo: https://github.com/sywoolab/sy-workspace

## 코드 수정 시 규칙

- 워크플로우(`.github/workflows/`) 수정 후 반드시 `workflow_dispatch`로 테스트
- 스크립트 수정 시 로컬 실행으로 사전 검증 (환경변수 세팅 필요)
- `workout/workout_log.json`, `workout/workout_schedule.json`은 다른 에이전트가 동시 수정할 수 있음 → git pull 후 작업

## 운동 데이터

- 마스터 규칙: `workout/_masters/WORKOUT_MASTER.md`
- 분석 알고리즘: `workout/_masters/WORKOUT_ALGORITHM.md`
- 운동 데이터 수정 시 위 마스터 문서를 반드시 읽고 준수
