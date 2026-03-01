---
name: mystock-telegram-update
description: Update MyStock DB from pasted Telegram account tables. Use for preview/apply of Korean broker balance tables.
metadata:
  {
    "openclaw":
      {
        "emoji": "📩",
        "requires": { "bins": ["python3", "sqlite3"] },
      },
  }
---

# MyStock Telegram Update

## 목적
텔레그램에 붙여 넣은 한국어 계좌 표(예: 미래에셋 잔고표)를 파싱해서 `skills/mystock/data/mystock.db`를 업데이트한다.

핵심 원칙:
- 먼저 **preview(미리보기)**
- 사용자가 확인하면 **apply(적용)**
- 적용 후 **대시보드 재생성**

## 입력 형식
다음 형식 중 하나를 지원:
- `[계좌번호]` + 헤더(`상품명`, `보유수량`/`잔고수량`, `현재가`, `평균매입가`/`매입단가`, `평가금액`)
- 탭 구분 또는 다중 공백 구분

계좌번호 매핑:
- `230-5002-8842-0` -> `DC`
- `364-5400-8768-0` -> `FUND`
- `001-1024-2048-0` -> `IRP`
- `079-99-012313` -> `USStock`

## 실행 스크립트
- 파서/업데이터: `skills/mystock/scripts/update_snapshot_from_korean_table.py`
- 대시보드 생성: `skills/mystock/scripts/run_daily_dashboard.sh`

## 표준 절차
1. 텔레그램 원문은 우선 `--text`로 직접 전달한다.
2. 원문이 너무 길 때만 워크스페이스 내부 임시파일(`skills/mystock/data/tmp_*.txt`)을 사용한다.
3. preview 실행해서 파싱 결과를 확인한다.
4. 사용자에게 계좌/행 수/총액/미매칭 종목을 보여주고 적용 여부를 확인한다.
5. 사용자가 승인하면 `--apply`로 반영한다.
6. 대시보드를 재생성한다.

## 명령 예시
```bash
python3 skills/mystock/scripts/update_snapshot_from_korean_table.py \
  --db skills/mystock/data/mystock.db \
  --text "$TELEGRAM_TEXT" \
  --pretty
```

```bash
python3 skills/mystock/scripts/update_snapshot_from_korean_table.py \
  --db skills/mystock/data/mystock.db \
  --text "$TELEGRAM_TEXT" \
  --apply \
  --pretty
```

```bash
bash skills/mystock/scripts/run_daily_dashboard.sh
```

## 주의
- 적용 모드는 DB 백업을 자동 생성한다.
- 입력 표에 없는 종목은 삭제하지 않는다(명시된 행만 업데이트).
- `미국달러` 행이 있으면 해당 `현재가`를 USD/KRW로 우선 사용한다.
