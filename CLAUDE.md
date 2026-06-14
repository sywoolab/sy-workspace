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

**검증 절차:**
1. A, B, C 결과를 비교. 3개 모두 일치 → 채택
2. 불일치 시 → **원문을 직접 인용**하여 차이 원인 보고. 사용자 판단 요청
3. **절대 금지**: 불일치를 임의로 평균내거나 다수결로 처리하지 않는다

### 레드팀 검증 (필수)

- 결과물 완성 후 **독립 서브에이전트**로 레드팀 검증 실행
- 검증 대상: 수치 정합성, 출처 유효성, 논리적 일관성
- **트리거 기준**: 3-에이전트 검증을 실행한 작업 + 코드 작성/수정 완료 시. 단순 설명/질의에는 불필요
- 레드팀 없이 최종 산출물을 사용자에게 전달하는 것은 **L0 위반**

### 정확성

- 모르면 WebSearch. 그래도 모르면 "모른다"
- **뇌피셜 금지**. 추정으로 답하지 않는다
- 수치에는 반드시 출처 명시

### 오류 보고

오류 발생 시 변명 금지. L0 §"오류 보고 프로토콜" 5단계 양식 강제:
1. **오류 내용** — 무엇이 틀렸는지 팩트만
2. **위반한 규칙** — 마스터·L0·L1 어떤 조항인지 명시
3. **근본 원인** — 로직 누락·적용 범위 착각·검증 미수행 등
4. **재발 방지** — 프로세스·마스터·코드 어디를 수정해야 하는지
5. **조치 완료 여부** — 수정 적용 내역 또는 미적용 사유

원칙: 동일 유형 반복 시 마스터 파일 구조적 결함으로 간주하고 마스터 개선.

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

## 에러 알림 필수 — SystemExit 패턴 (필수 — 2026-05-24 도입)

> 2026-05-08 INBOX #4 후속. cron 스크립트가 exit code 1+ 로 종료해도 텔레그램에 도달 못 하면 사용자는 묵음 실패를 인지 못 함. 모든 신규/기존 운영 스크립트에 아래 try/except 패턴 의무 적용.

### 표준 패턴

`if __name__ == '__main__':` 블록을 다음과 같이 작성한다 (운동·IB·부동산 모든 cron-driven 스크립트):

```python
import sys
import traceback

if __name__ == '__main__':
    try:
        sys.exit(main())
    except SystemExit as e:
        if e.code not in (0, None):
            try:
                send_telegram(f"❌ {SCRIPT_NAME} 오류 (exit {e.code})")
            except Exception:
                pass
        raise
    except Exception:
        try:
            send_telegram(f"❌ {SCRIPT_NAME} 예외\n{traceback.format_exc()[:1500]}")
        except Exception:
            pass
        sys.exit(1)
```

### 적용 대상
- `workout/scripts/*.py` (garmin_sync, workout_alert, notify_log_change, notify_schedule_change)
- `ib/scripts/*.py`, `realestate/scripts/*.py` 등 모든 cron-driven 스크립트
- 신규 스크립트 작성 시 본 패턴 의무 (PR 검토 시 누락 확인)

### 검증
- 스크립트 수정 후 인위적 실패(존재하지 않는 BOT_TOKEN 등) 1회 dry-run으로 텔레 알림 도달 확인
- 알림 누락 시 즉시 패턴 추가 + 재배포

## 자동화 산출물 검증 (필수 — 2026-05-10 도입)

> 가민 sync 6회 실패 사례에서 도출. 자동화 스크립트가 "성공" 메시지를 출력하면서도 실제 산출물에는 누락이 있는 silent fail 패턴을 차단한다.

### 원칙

자동화 스크립트는 외부 API 응답 수신만으로 "성공"을 보고하지 않는다. **자기 산출물(workout_log entry, 알림 발송 결과 등)의 변화량까지 검증**한 후에만 success 종료한다.

### 강제 규칙

1. **산출물 변화량 비교 의무**: API에서 N건 fetch했는데 산출물에 0건 추가되면 의심 알림 발송 (SKIP 사유 포함)
2. **SKIP 사유 텔레그램 노출**: 필터(cutoff, dedupe, type 미지원 등)로 SKIP 발생 시 텔레그램 메시지에 누적 SKIP 건수+사유 표시. 매 sync마다 사용자가 인지 가능
3. **땜질 금지**: 특정 회차만 수동 보정(예: garmin_id 강제 추가 commit)하지 않는다. 동일 사고가 다음 회차에 또 발생함. **반드시 root cause 코드 수정으로 재발 차단**
4. **알려진 이슈 SLA**: feedback memory 또는 INBOX에 등록된 자동화 미해결 이슈는 **2주 이내 코드 수정 또는 INBOX 승격**. 만료 시 자동화 신뢰도가 무너진다

### 적용 대상

`workout/scripts/garmin_sync.py`, `workout/scripts/workout_alert.py`, `ib/scripts/*`, `realestate/scripts/*` 등 모든 cron-driven 자동화 스크립트.

### 검증 패턴 (참조 구현)

```python
# sync 시작 전후 산출물 차이 측정
before_count = len(workout_log)
new_activities = process()
after_count = len(workout_log)
delta = after_count - before_count
fetched = len(activities)
skipped = fetched - delta

if fetched > 0 and delta == 0:
    send_telegram(f"⚠️ {fetched}건 fetch했으나 모두 SKIP — 사유 확인 필요")
elif skipped > 0:
    # 정상 SKIP도 노출 (cutoff/dedupe 등)
    msg += f"\n[SKIP {skipped}건: {skip_reasons_summary}]"
```

## 운동 데이터

- 마스터 규칙: `workout/_masters/WORKOUT_MASTER.md`
- 분석 알고리즘: `workout/_masters/WORKOUT_ALGORITHM.md`
- 운동 데이터 수정 시 위 마스터 문서를 반드시 읽고 준수

## 운동 코칭 프로토콜

### 필수 참조 파일 (운동 관련 대화 시)
운동/철인3종 관련 대화가 시작되면 아래 파일을 반드시 읽고 시작한다:
1. `workout/_masters/WORKOUT_MASTER.md` — 규칙
2. `workout/_masters/WORKOUT_ALGORITHM.md` — 분석 알고리즘
3. `workout/workout_log.json` — 최근 기록 (마지막 7일)
4. `workout/workout_schedule.json` — 현재 스케줄 + overrides

### 2026 대회 일정 (전부 접수 완료, 스케줄 짤 때 반드시 참조)
- **05.10 대구 시장배** — 첫 대회 스탠다드. 전날(5/9) 수성못 OW 연습
- **06.07 쉬엄쉬엄 한강 3종** — 축제형 (수영1km+자전거20km+러닝10km). 서울 뚝섬
- **06.21 한강리버크로스스위밍** — OW 수영 2km 도강. 일요일 11시. 전날 회사 1박2일
- **06.27~28 고령 대가야** — 6/27(토) 이동, **6/28(일) 대회**. 두 번째 공식 스탠다드. 낙동강 은행나무숲
- **하반기 TBD** — 거제(08.27) or 충주(10.24) 중 택1
- 대회 간 간격: 대구→한강3종 4주 / 한강3종→한강수영 2주 / 한강수영→대가야 6일

### 고정 스케줄 (변경 불가 — 에이전트가 임의로 "쉬라"고 하면 안 됨)

아래는 **실제 수업 등록된 고정 일정**이다. 스케줄 제안 시 이 일정이 있다는 것을 반드시 인지하고 반영한다. 컨디션에 따라 쉬라고 할 수는 있지만, **일정 자체를 모르고 빠뜨리면 안 된다.**

```
월 🏊 수영 수업 (06시)
수 🏊 수영 수업 (06시)
금 🏊 수영 수업 (06시)
토 🏊 수영 개인강습
```

### 사용자 운동 스타일 — 반드시 준수
1. **러닝 빌드업 스타일**: 워밍업 3km(6:00~6:20) 필수. 이후 점진적으로 올림. 처음부터 페이스 넣으라고 강요하지 않는다.
2. **"올린 페이스를 내리지 않는 빌드업"**이 핵심 목표
3. 이븐 페이스를 강요하면 부상 위험 — 본인이 가장 잘 앎

### 컨디션 판단 기준
- **같은 페이스에서 HR 변화**로 회복 상태 판단
  - HR 145 이하에서 5:45 → 회복됨
  - HR 155+ → 미회복, Easy 또는 휴식
- **다리 vs 호흡**: 다리보다 심폐(호흡)가 먼저 한계. 심폐 회복이 핵심
- **수영 부하는 낮음**: 러닝 사이 회복일로 배치하는 것이 효과적

### VDOT 해석 주의
- 알고리즘 VDOT은 **보수적** (빌드업 워밍업이 평균을 깎음)
- 실질 능력은 알고리즘 VDOT +2~3 수준
- Easy/회복 러닝 데이터가 VDOT을 끌어내리는 구조적 문제 인지

### 자전거 평속 해석 주의
- GPS 평속에 신호/경사가 섞여있으면 과소평가됨
- 코스 확인 없이 "평속이 느리다"고 단정 금지
- 본인은 아마추어 선수 출신, 평지 항속 35km/h+

### 훈련 대시보드 (역사 기록 + 코칭 참조)

- **URL**: https://sywoolab.github.io/training-dashboard/
- **용도**: 3/16~현재 전체 훈련 기록의 역사적 스냅샷. gsync 시 자동 업데이트.
- **철학**: 역사책처럼 누적. 매 gsync 시 새 데이터 추가, 과거 기록 삭제 X.
- **백업**: sy-workspace 레포 `workout/data/training_report.html` + training-dashboard repo 이중 보관.

### 운동 코칭 시 필수 데이터 참조 (의무 — 추측 금지)

운동 관련 질문(컨디션·일정·목표·약점·오늘 운동 여부) 답변 시 메인 단독 추측 금지. 반드시 직접 Read:

| 참조 파일 | 무엇 확인 | 방법 |
| ------ | ------ | ------ |
| `workout_log.json` 최근 14일 | 실제 훈련 기록, 부하, 페이스 | python3 코드로 최근 14일 추출 |
| `workout/data/garmin_health.json` 최근 7일 | HRV last/weekly, RHR, 수면점수 | 직접 read |
| `workout_schedule.json` overrides | 다음 14일 계획 | overrides 섹션 |

hook 자동 첨부(workout_schedule, workout_log)로 이미 제공되지만, garmin_health는 추가 조회 필요.
**"컨디션 어때?" / "오늘 운동 해야 해?" 질문 시 garmin_health 7일 데이터 반드시 직접 확인**.

### 운동 리뷰 시 가민 상세 데이터 필수 (의무 — 2026-06-14 도입)

> 사용자 지시(2026-06-14): "자전거 리뷰할 때는 가민 상세데이터 다 읽어서 파워나 심박 같은 것까지 본 다음에 평가해줘."

특정 운동(라이드·러닝·수영·브릭)에 대한 **리뷰·평가·피드백** 요청 시, `workout_log.json`의 요약 metrics만으로 평가하는 것을 **금지**한다. 반드시 가민에서 **해당 활동의 상세 데이터를 직접 fetch한 뒤** 평가한다.

**필수 확인 항목 (종목별):**

| 종목 | 반드시 읽을 상세 지표 |
| ------ | ------ |
| 사이클 | avgPower / normPower(NP) / maxPower / max20MinPower / IF / TSS / 파워존 분포 · 평균·최대 HR / HR존 분포 · 케이던스 · 좌우밸런스 · 누적상승(elevationGain) · VO2max |
| 러닝 | 평균·최대 HR / HR존 분포 · 페이스 스플릿(랩별) · 케이던스 · GAP(경사보정) · 누적상승 · 유산소 TE |
| 수영 | 100m 페이스 · SWOLF · 스트로크수 · 구간별 페이스 · 평균 HR |

**절차:**
1. `garminconnect`로 해당 활동의 전체 필드(summary) + HR존(`get_activity_hr_in_timezones`) fetch. 요약 KEYS만 뽑는 `run_garmin_query.py`로는 파워·존이 빠지므로 부족 시 전체 dict 덤프.
2. **체감 vs 데이터 괴리**(마스터 §8)를 반드시 교차 — 사용자 체감과 상세 지표가 다르면 둘 다 보고 + 원인 가설(수면·기온·숙취·심리 등) 제시.
3. 계획(Z2 등) 대비 실제 존 분포를 대조해 "계획대로였는지" 판정.
4. 리뷰 본문에 상세 수치를 출처와 함께 인용. 요약값 추측 금지(L0 §"뇌피셜 금지").

**근거**: 평균 HR·평속 같은 요약값만으로는 "심박당 파워(효율)", "존 분포", "언덕 보정 출력"을 알 수 없어 오판한다. 2026-06-14 사례 — 사용자가 "컨디션 안 좋게 느꼈는데 퍼포먼스 좋다" 보고. 요약(평속 24km/h)만 보면 평범했지만, 상세(NP 200W·IF 0.82를 평균 HR 139로 + 601m 클라이밍)를 봐야 "유산소 효율 우수"라는 정확한 평가가 나왔다.

### 운동 데이터 수동 요청 시
가민 동기화가 자동이지만 누락될 수 있음. 사용자가 "운동 업데이트해줘"라고 하면:
1. `git pull`
2. `garmin_sync.py sync` 실행 (환경변수: .env에서 로드)
3. workout_log.json 최근 데이터 확인
4. 분석 결과 보고

### 스케줄 변경 시
- `workout_schedule.json`의 overrides에 `source: "manual"`, `auto: false` 필수
- adaptive_scheduler가 manual override를 덮어쓸 수 있음 → `auto: false`로 보호
- 텔레그램 알림 반영 시 직접 메시지 전송

### 스케줄/일정 답변 시 SSOT 직접 read 의무 (필수)

운동 일정·스케줄·"내일/모레/D-N에 뭐 해야 함" 류 질문 답변 시:

1. **답변 작성 전 반드시 `workout_schedule.json`의 `overrides` 섹션을 Read 도구로 직접 확인**한다.
2. 답변 본문에 출처 라벨 인용: `schedule_overrides 5/6: 러닝 4km Easy [user-confirmed]`
3. **금지**: `workout_alert.py`의 `WEEK*_SCHEDULE` / `DEFAULT_SCHEDULE` 상수만으로 답변하는 것. 이 상수는 default fallback이며, 실제 일정은 `overrides`가 우선한다.
4. UserPromptSubmit hook이 `workout_schedule.json` 자동 첨부하므로 컨텍스트에 있는 그것을 1차로 사용한다. 단, 컨텍스트 첨부분이 잘렸거나 의심되면 Read로 재확인.

**근거**: 코드의 default 상수와 사용자가 manual override한 실제 일정이 다를 수 있다. 2026-05-05 사례에서 default가 "수=자전거 30분"이었지만 실제 override는 "5/6 러닝 4km Easy"였고, 코드 상수만 보고 답변해 사용자 지적을 받았다 (L0 §"전수 확인 원칙" 위반).

## 데이터 변경 = "반영"의 정의 (필수 — 2026-05-24 도입)

SSOT 보호 파일(`workout_schedule.json`, `workout_log.json`, `ib/watchlist*`, `real-estate/*`, `_claude_memory/*`)을 변경할 때 사용자가 "반영", "수정", "변경", "적용" 등 표현을 쓰면 **기본값은 로컬 Edit + git commit + git push까지**다.

로컬 Edit만으로는 텔레그램·HTML(training-dashboard)·다른 머신·GitHub Actions 어디에도 도달 못 함 → "반영" 아님.

### 예외 (사용자 명시 시에만)
- "commit 하지 마", "push는 나중에", "로컬에서만" → 그 단계 생략
- 모호한 발화 ("그냥 X만 반영해") → commit/push 빼라는 의미로 해석 금지. "X에만 집중하고 부수작업 빼라"의 자연 해석 우선. commit/push는 반영의 핵심 절차이지 부수작업이 아님

### 위반 시 보고
- Edit만 한 상태로 turn 종료할 때 응답에 **"⚠️ 로컬 Edit만 됨 — 텔레/HTML 미반영. push 필요 시 알려주세요"** 1줄 명시 의무
- 사후 발견 시 L0 §"오류 보고 프로토콜" 적용. 사용자 발화·표현 탓 표현 금지 ("오역", "요청대로" 등) → 메인 행동의 구조적 원인 분석

### 인프라 (2026-05-24 도입)
- `workout/scripts/notify_schedule_change.py` — schedule 변경 시 텔레 즉시 알림 (HEAD~1 vs HEAD 비교)
- `.github/workflows/schedule-update.yml` — push trigger
- 메인이 push 정상 수행 시 텔레·HTML·cron alert 3자 자동 동기화

### 배경
2026-05-24 메인이 workout_schedule.json Edit 후 commit/push 누락 → 텔레/HTML 미반영 사고. 사용자가 발견 후 "사용자 발화 오역" 표현으로 메인이 책임 전가 → 이중 과실. 상세: 메모리 `feedback_data_change_reflection.md`.
