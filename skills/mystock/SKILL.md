---
name: mystock
description: Manage MyStock database from Telegram. Use this for account total queries, date/symbol lookups, and DB updates from Korean account tables or trade messages.
metadata:
  {
    "openclaw":
      {
        "emoji": "📈",
        "always": true,
        "requires": { "bins": ["python3", "sqlite3"] },
      },
  }
---

# MyStock Telegram Ops

## Purpose
- 텔레그램에서 들어오는 주식/연금 관련 요청을 `skills/mystock/data/mystock.db` 기준으로 처리한다.
- `조회`와 `업데이트`를 모두 지원한다.
- 사용자가 "적용"을 명시하지 않으면 업데이트는 항상 preview부터 시작한다.

## DB Paths
- DB: `skills/mystock/data/mystock.db`
- Dashboard script: `skills/mystock/scripts/run_daily_dashboard.sh`

## Query Commands (read-only)

계좌 총액/날짜별 총액:
```bash
python3 skills/mystock/scripts/query_mystock.py totals --db skills/mystock/data/mystock.db --account DC --pretty
```

특정 날짜 기준 총액:
```bash
python3 skills/mystock/scripts/query_mystock.py totals --db skills/mystock/data/mystock.db --account USStock --date 20260301 --pretty
```

종목 보유 조회(티커/이름):
```bash
python3 skills/mystock/scripts/query_mystock.py position --db skills/mystock/data/mystock.db --account DC --symbol "TIGER 화장품" --pretty
```

체결/거래 이력 조회:
```bash
python3 skills/mystock/scripts/query_mystock.py trades --db skills/mystock/data/mystock.db --account USStock --symbol STX --limit 20 --pretty
```

## Update Commands

### A) 계좌 잔고 표(스냅샷) 업데이트
입력: `[계좌번호]` + `상품명/보유수량/현재가/평균매입가/평가금액` 표

기본 원칙:
- 텔레그램 원문은 가능하면 `--text`로 직접 넘긴다.
- `write` 도구로 `/tmp/*` 파일을 만들지 않는다(환경에 따라 실패 가능).

Preview:
```bash
python3 skills/mystock/scripts/update_snapshot_from_korean_table.py --db skills/mystock/data/mystock.db --text "$TELEGRAM_TEXT" --pretty
```

Apply:
```bash
python3 skills/mystock/scripts/update_snapshot_from_korean_table.py --db skills/mystock/data/mystock.db --text "$TELEGRAM_TEXT" --apply --pretty
```

### B) 매매 체결 문자 업데이트
입력: 미래에셋 문자(매수/매도, 수량, 단가/금액, 계좌)

Preview:
```bash
python3 skills/mystock/scripts/update_trade_from_korean_message.py --db skills/mystock/data/mystock.db --text "$TELEGRAM_TEXT" --pretty
```

Apply:
```bash
python3 skills/mystock/scripts/update_trade_from_korean_message.py --db skills/mystock/data/mystock.db --text "$TELEGRAM_TEXT" --apply --pretty
```

## After Apply
- 스냅샷/거래를 적용했다면 대시보드를 다시 만든다.
```bash
bash skills/mystock/scripts/run_daily_dashboard.sh
```

## Telegram Response Rules
- 총액/조회 질문에는 반드시 DB 조회 결과 숫자를 포함해서 답한다.
- "모른다/확인 불가"라고 답하기 전에 위 조회 명령을 먼저 실행한다.
- 업데이트는 preview 결과(계좌, 반영 건수, 미매칭, 예상 변경액)를 먼저 보여주고, 사용자가 승인하면 apply한다.
- 사용자가 "바로 반영해"라고 명시한 경우에만 preview 생략 가능.
