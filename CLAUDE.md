# 봇/자동화 규칙 (L1)

> 이 파일은 `~/CLAUDE.md`(L0 절대규칙)을 **상속**한다.
> 작업 시작 전 반드시 L0을 먼저 읽은 후 이 파일을 적용한다.
> L0과 충돌 시 L0이 우선한다.

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

- Chat ID: `TELEGRAM_CHAT_ID` (모든 봇 공통)
- GitHub repo: https://github.com/sywoolab/sy-workspace

## 코드 수정 시 규칙

- 워크플로우(`.github/workflows/`) 수정 후 반드시 `workflow_dispatch`로 테스트
- 스크립트 수정 시 로컬 실행으로 사전 검증 (환경변수 세팅 필요)
- `workout_log.json`, `workout_schedule.json`은 다른 에이전트가 동시 수정할 수 있음 → git pull 후 작업

## 운동 데이터

- 마스터 규칙: workspace의 `_masters/WORKOUT_MASTER.md`
- 분석 알고리즘: workspace의 `_masters/WORKOUT_ALGORITHM.md`
- 운동 데이터 수정 시 위 마스터 문서를 반드시 읽고 준수
