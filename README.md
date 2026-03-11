# SY Workspace

텔레그램 봇 자동화 시스템

## Bots

| 봇 | 용도 |
| --- | --- |
| SY Workspace (@SY_workspace_bot) | 업무 리마인드 + 딜 기업 모니터링 |
| SY IB News (@SY_IB_News_bot) | IB 업계 뉴스 클리핑 |

## Workflows

| Name | Schedule (KST) | Description |
| --- | --- | --- |
| daily-reminder | 매일 07:00 | tasks.md 기반 할 일 알림 |
| dart-alert | 평일 09~17시 /30분 | DART IB 공시 모니터링 |
| watchlist-alert | 평일 09~17시 /30분 | Watchlist 기업 공시+뉴스 |
| ib-news | 평일 08:00, 14:00 | IB 뉴스 클리핑 20건 |

## 기업 추가/삭제

`watchlist.json` 수정 후 push하면 자동 반영됩니다.
