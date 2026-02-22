# CHLOE 구현 계획 (기획안 v4.0 100% 준수)

본 계획은 `CHLOE AI Trading System — 최종 기획안 v4.0 (Final).md`의 파일명/경로/로직을 그대로 따른다.

## 0) 고정 원칙
- 디렉터리명은 `cache/`를 사용한다. (`runtime_cache/` 사용 금지)
- Python 모듈 구조는 기획안의 13개 파일만 사용한다.
- 기획안에 명시된 JSON 파일명(12개)과 경로를 그대로 사용한다.
- `tools.py`는 섹션 12의 최종 도구 목록을 동일 이름/역할로 JSON Schema까지 구현한다.
- `ai_brain.py`는 섹션 11 Part A/B/C 시스템 프롬프트 전문을 코드에 그대로 포함하고, Claude 요청에 `cache_control: {"type": "ephemeral"}`를 적용한다.
- Claude 호출은 별도 스레드 비동기로 수행해 메인 루프를 차단하지 않는다.

## 1) 파일 구조 (기획안 13개 고정)
1. `config.py`
2. `config_secret.py`
3. `data.py`
4. `indicators.py`
5. `logger.py`
6. `paper_trading.py`
7. `executor.py`
8. `tools.py`
9. `ai_brain.py`
10. `telegram_handler.py`
11. `websocket_client.py`
12. `scheduler.py`
13. `main.py`

> 추가 권장 파일(`models.py`, `stats.py`, `state_manager.py` 등) 생성하지 않음.

## 2) cache/ JSON 파일 목록 (기획안 정의 12개)
`cache/` 디렉터리에 아래 12개 파일을 생성/사용한다.

1. `cache/current_analysis.json`
2. `cache/scenarios.json`
3. `cache/paper_position.json`
4. `cache/trade_log.json`
5. `cache/insights.json`
6. `cache/cold_insights.json`
7. `cache/principles.json`
8. `cache/performance.json`
9. `cache/analysis_routine.json`
10. `cache/alerts.json`
11. `cache/daily_loss.json`
12. `cache/system_state.json`

## 3) 단계별 구현 계획 (파일별 생성/수정)

### Step 1. 설정 및 하드룰 확정
- 생성/수정: `config.py`, `config_secret.py`
- 반영 내용:
  - `config.py`는 기획안 섹션 13-1의 전체 변수 집합을 그대로 반영:
    - `TRADING_STAGE`, `SYMBOL`, `LEVERAGE`, `MARGIN_MODE`
    - `MAX_LOSS_PERCENT`, `MAX_CONCURRENT_POSITIONS`, `LEVERAGE`, `MAX_ENTRY_SLIPPAGE`
    - `STAGE3_DAILY_LOSS_HARD_LIMIT`
    - `REST_BASE`, `WS_PUBLIC`, `WS_PRIVATE`
    - `TIMEFRAMES`, `CANDLE_COUNTS`, `UPDATE_COUNTS`
    - `CLAUDE_MODEL`, `MAX_CLAUDE_CALLS_PER_HOUR`, `EXEMPT_TRIGGERS`, `MAX_ITERATIONS`
    - `SIM_TAKER_FEE`, `SIM_MIN_SLIPPAGE`
    - `SHEETS_SYNC_INTERVAL`, `TELEGRAM_POLL_INTERVAL`, `HEARTBEAT_INTERVAL`, `ALERT_THROTTLE_SECONDS`
    - `COLD_STORE_DAYS`, `STATS_REBUILD_INTERVAL`, `POSITION_CHECK_INTERVAL`
    - `OVERFIT_FAILURE_THRESHOLD`, `INSIGHT_CONFIDENCE`
    - `MIN_TRADES_FOR_TRANSITION`, `MIN_DAYS_FOR_TRANSITION`, `MAX_MDD_FOR_TRANSITION`
  - `config_secret.py`는 기획안 섹션 13-2 변수명을 그대로 반영:
    - `OKX_API_KEY`, `OKX_SECRET_KEY`, `OKX_PASSPHRASE`
    - `CLAUDE_API_KEY`
    - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
    - `GOOGLE_SHEETS_CREDS_FILE`, `GOOGLE_SHEETS_SPREADSHEET_ID`

### Step 2. OKX 데이터 계층
- 생성/수정: `data.py`
- 반영 내용:
  - 함수 목록(기획안 섹션 13-3 기준):
    - `get_candles()`, `get_ticker()`, `get_balance()`, `get_positions()`
    - `get_funding_rate()`, `get_instruments()`
    - `place_order()`, `cancel_order()`, `amend_algos()`, `get_algo_orders_pending()`
  - 서명/인증 헤더 생성
  - 에러 처리(모든 요청 공통):
    - timeout 10초
    - HTTP 429: `Retry-After` 대기 → 최대 3회 재시도, 지수 백오프(2s, 4s, 8s)
    - HTTP 5xx: 동일 재시도 로직
    - 3회 실패 시 `RetryLimitError` 예외 발생
  - 상품 사양 자동 검증

### Step 3. 지표/시장 태깅
- 생성/수정: `indicators.py`
- 반영 내용:
  - 지표 계산 함수
  - 시장 상태 태깅
  - AI 입력에 필요한 요약 컨텍스트 반환

### Step 4. 기록/통계/캐시 무결성
- 생성/수정: `logger.py`
- 생성: `cache/*.json` 12개
- 반영 내용:
  - JSON 읽기/쓰기 + `.bak` 복구
  - 로그 append/merge 유틸
  - 기획안의 통계 재계산(`daily_stats_rebuild()` 포함)
    - 계산 항목: 전체 승률, 평균 R:R, 총 손익, 최대 연승/연패, MDD
    - `market_env_tag`별 세분화 통계(예: `trend=up + volatility=high` 조합 승률)
    - 지표별 성과 비교 테이블(A/B 테스트: 지표 활성 거래의 승률/R:R)
    - 환경 간 Pearson 상관계수(구현 시점 적용)
    - 결과를 `cache/performance.json`에 덮어쓰기
    - `performance.json["last_rebuild"]` 타임스탬프 갱신
    - 트리거 조건: `now - last_rebuild >= STATS_REBUILD_INTERVAL(86400초)`
    - 다음 price-feed 이벤트에서 실행, 독립 Claude 호출 없이 `meta_check` 트리거와 같은 iteration에서 처리
  - Google Sheets 동기화 함수는 `logger.py`에 구현하고, 탭/JSON 매핑을 명시:
    - `CHLOE_차트분석` ↔ `cache/current_analysis.json`
    - `CHLOE_시나리오` ↔ `cache/scenarios.json`
    - `CHLOE_인사이트` ↔ `cache/insights.json`
    - `CHLOE_원칙` ↔ `cache/principles.json`
    - `CHLOE_매매기록` ↔ `cache/trade_log.json`
    - `CHLOE_성과` ↔ `cache/performance.json`
  - 동기화 실행 타이머(5분 주기)는 `scheduler.py`에서 호출하고, 실제 write 로직은 `logger.py`가 담당

### Step 5. Stage 1 모의 엔진
- 생성/수정: `paper_trading.py`
- 반영 내용:
  - 가상 포지션/체결/수수료/슬리피지/펀딩비
  - `cache/paper_position.json` 상태 동기화

### Step 6. 주문 실행기/강제 규칙
- 생성/수정: `executor.py`
- 반영 내용:
  - 진입 전 체크리스트(기획안 섹션 13-3, 순서 고정):
    1) SL 존재 확인
    2) 포지션 크기 계산 → 손실 ≤ 5% 검증
    3) 동시 포지션 수 ≤ 1 확인
    4) 레버리지 ≤ 10× 확인
    5) 슬리피지 검증(`acceptable_price_range` → `MAX_ENTRY_SLIPPAGE`)
    6) Stage 3: 일일 누적 손실 15% 확인
  - `TRADING_STAGE` 분기 실행:
    - 1 → `paper_trading`
    - 2 → OKX demo
    - 3 → OKX live
  - 포지션 크기 조정 시 사유 로깅 + CHLOE 통보
  - 중복 주문 방지: `clOrdId = "chloe_{YYYYMMDD}_{sequence}"`
  - SL 방향 검증:
    - long이면 새 SL ≥ 기존 SL
    - short이면 새 SL ≤ 기존 SL
  - SL 등록 실패 처리(기획안 5-7):
    - 진입 직후 SL/TP algo 등록 시도
    - 5초 타임아웃 내 WebSocket 또는 REST로 등록 확인
    - 확인 실패 시 즉시 전량 시장가 청산

### Step 7. Claude 도구 스키마
- 생성/수정: `tools.py`
- 반영 내용: 사용자 지적 목록(섹션 12 기준)의 도구를 명시적으로 구현
  1) `get_candles`
  2) `save_analysis`
  3) `save_scenario`
  4) `set_alert`
  5) `remove_alert`
  6) `open_position`
  7) `close_position`
  8) `modify_sl`
  9) `modify_tp`
  10) `save_trade_memo`
  11) `save_insight`
  12) `save_principle`
  13) `update_analysis_routine`
  14) `conclude_review`
  15) `send_telegram`

> 개수 표기가 상이할 수 있으므로, 실제 구현은 기획안 섹션 12의 최종 목록/스키마를 소스 오브 트루스로 삼아 1:1로 맞춘다.

### Step 8. AI 브레인
- 생성/수정: `ai_brain.py`
- 반영 내용:
  - Part A/B/C 시스템 프롬프트 전문 하드코딩
  - `cache_control: {"type":"ephemeral"}` 적용
  - Claude 호출을 별도 스레드 비동기로 실행
  - 실패 재시도/타임아웃/결과 파싱

### Step 9. 텔레그램 핸들러
- 생성/수정: `telegram_handler.py`
- 반영 내용:
  - `/pause`, `/resume`, `/panic` 명령
  - `/pause`/`/panic` 시 `cache/system_state.json`에
    `paused`, `pause_reason`, `market_snapshot` 기록
  - 포지션 소실 대응(기획안 10-5):
    - 텔레그램 질문 전송
    - 5분 대기 타이머
    - 무응답 시 자동 OKX 이력 조회 및 반영

### Step 10. 실시간 피드
- 생성/수정: `websocket_client.py`
- 반영 내용:
  - WS 채널 3개를 명시적으로 구독:
    - `tickers` (가격)
    - `orders` (주문 상태)
    - `positions` (포지션)
  - WS 연결/구독/재연결(최대 횟수)
  - 재연결 시 REST로 포지션 재조회 → 내부 상태와 비교
  - 불일치 발생 시 REST 데이터를 진실(source of truth)로 삼아 내부 상태 강제 업데이트
  - 재연결 실패 시 REST 폴백

### Step 11. 스케줄러
- 생성/수정: `scheduler.py`
- 반영 내용:
  - 트리거 루프 및 잡 관리
  - 배칭 로직(기획안 8-2):
    - 마지막 Claude 호출 후 2~3분 내 비긴급 트리거는 묶어서 처리
  - Google Sheets 5분 주기 동기화 타이머에서 `logger.py`의 동기화 함수 호출
  - UptimeRobot 하트비트 GET 호출
  - 24시간마다 cold store 정리:
    - `cache/insights.json`에서 `last_used_at` 기준 `COLD_STORE_DAYS`(14일) 초과 항목을 `cache/cold_insights.json`으로 이동
  - `daily_stats_rebuild` 트리거:
    - price-feed 이벤트마다 `performance.json["last_rebuild"]` 확인
    - `now - last_rebuild >= STATS_REBUILD_INTERVAL`이면 `logger.daily_stats_rebuild()` 호출
    - 같은 iteration 내에서 `meta_check` 트리거 실행(추가 Claude 호출 없음)

### Step 12. 통합 진입점
- 생성/수정: `main.py`
- 반영 내용:
  - 기획안 5-1 시작 시퀀스 9단계를 순서대로 수행:
    1) `cache/` 디렉터리 + 12개 JSON 무결성 확인 (없으면 기본값 생성, 손상 시 `.bak` 복구)
    2) `system_state.json` 확인 → `"normal"` 아니면 비정상 종료로 판단 → 텔레그램 알림
    3) API 연결 테스트: OKX REST, Claude API, Google Sheets → 실패 시 텔레그램 알림 + 재시도
    4) OKX 계약 사양 조회 (`ctVal`, `lotSz`)
    5) OKX 레버리지 설정
    6) 비정상 종료였다면: OKX 마지막 종료 이후 체결 내역 조회 → CHLOE에게 보고
    7) WebSocket 연결 시작 (`tickers`, `orders`, `positions`)
    8) 텔레그램 봇 폴링 시작
    9) 첫 가격 피드 수신 → `first_analysis` 트리거
  - 모듈 wiring
  - 정상 종료 시 `cache/system_state.json`에 shutdown=normal 기록
  - 비정상 종료 감지/복구 플로우
  - 최상위 예외 처리 + 텔레그램 알림

## 4) 테스트/검증 순서
1. 정적 검사: import, 설정 로딩, cache 12개 파일 존재/스키마 점검
2. Stage 1 시나리오: 진입→SL 등록 확인→청산→로그/통계 반영
3. 예외 시나리오:
   - SL 등록 확인 실패 후 강제 청산
   - WS 끊김 후 재연결/REST 폴백
   - 포지션 소실 질의 후 5분 무응답 자동 처리
4. 배칭 검증:
   - 2~3분 윈도우 내 비긴급 트리거 묶음 실행 확인
5. 재시작 복구:
   - 비정상 종료 플래그 감지
   - `.bak` 복구
   - 종료 이후 체결 조회 반영

## 5) 구현 체크리스트
- [ ] `cache/` 경로만 사용
- [ ] JSON 12개 파일명 100% 일치(특히 `insights.json` 포함, `lessons_learned.json` 제외)
- [ ] 기획안 정의 모듈 집합만 사용
- [ ] `tools.py` 도구 목록/스키마 완전 일치
- [ ] Part A/B/C 프롬프트 전문 반영
- [ ] Claude 비동기 스레드 호출
- [ ] 배칭(2~3분) 반영
- [ ] SL 등록 실패 5초 검증 후 즉시 청산
- [ ] `/pause` `/panic` 상태 기록 필드 반영
- [ ] 포지션 소실 5분 대기 후 자동 조회 반영
