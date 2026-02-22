**문서 버전**: 4.1 (Final) **작성일**: 2026-02-22 **용도**: 이 문서 하나만으로 어떤 AI 개발자든 동일한 시스템을 구현할 수 있어야 한다. **검토 이력**: o3 Pro, Grok, Gemini 3.1 Pro, 추가 다수 AI 반박 검토 완료. 사용자 최종 승인 완료.

### 수정 이력 (v4.0 → v4.1)

|변경 내용|변경 이유|
|---|---|
|insights.json에 `reasoning_chain`, `trigger_conditions`, `warnings` 추가|추론 경로 소실 방지|
|principles.json에 `trigger_conditions`, `alert_level`, `trigger_history`, `cooloff_remaining` 추가|자체 원칙 자동 상기|
|executor.py 7단계 소프트 가드레일 + 쿨오프|자율성 유지 + 학습 일관성|
|logger.py 품질 검사 + 학습 건강 보고서 + regime_shift_warning|인사이트 품질 검증|
|도구 16개 (`get_insight_detail` 추가, `save_insight`에 archive/invalidate 통합)|챕터링 + 맥락 접근|
|최소 1계약 강제 삭제 → 리스크 초과 시 진입 차단|하드룰 #2 충돌 해소|
|Stage 전환 수동 승인 필수화|실전 자동 전환 방지|
|periodic_review 활성 조건 추가, 호출 제한 15회, 트리거 우선순위|비용 효율 + 병목 해소|
|가격 stale 처리, 슬리피지 동적 완화, 주문 폭주 방지, REST 차등 간격|안전성 강화|
|env 매칭 Pre-fetching + insight_detail 인메모리 캐시|긴급 진입 지연 해결|
|지정가 주문 만료/취소, clOrdId 형식, by_indicator 라벨링 명시|구현 명확성|
|연속 3패 / 일일 10% 시 immediate_review|학습 데이터 오염 방지|

---

## 0. 프로젝트 철학 및 궁극적 목표

### 0-1. 궁극적 목표

이 시스템의 이름은 **CHLOE(클로이)**이다. CHLOE는 Anthropic Claude API를 기반으로 동작하는 자율형 BTC 선물 트레이딩 AI이다. CHLOE가 **스스로 분석하고, 스스로 매매하고, 스스로 복기하고, 스스로 학습**하여, 장기적으로 독립적인 트레이더로 성장하는 시스템을 만드는 것이 궁극적 목표이다.

### 0-2. 설계 원칙

**원칙 1 — 자율성 우선**: CHLOE는 진입·청산·복기·학습을 자율 수행한다. 사용자는 텔레그램으로 관찰하고, 필요 시 대화로 개입한다. 사용자 확인(confirm)을 요구하지 않는다.

**원칙 2 — "왜"에 집중**: 시간 의존 분석을 최소화한다. 가격이 움직이는 이유, 구조가 바뀌는 이유에 집중한다. `[사용자 요청]`

**원칙 3 — 통계 기반 학습**: 인사이트는 느낌이 아닌 데이터에서 도출한다. `[사용자 요청]`

**원칙 4 — 기록의 자율성**: CHLOE가 기록의 형식, 내용, 분류를 스스로 결정한다. `[사용자 요청]`

**원칙 5 — 비용 효율**: Python이 처리 가능한 작업은 Python이 직접 수행하고, Claude는 "판단"이 필요한 시점에만 호출한다.

**원칙 6 — 학습 품질 보장** `[v4.1]`: 학습 결과물은 Python이 자동 품질 검사를 수행한다. 자율성은 유지하되 과최적화·편향·맥락 소실을 구조적으로 방지한다.

### 0-3. 태그 설명

`[사용자 요청]` — 사용자가 직접 요구한 사항. `[AI 합의]` — 다수 AI 검토 확정. `[설계 결정]` — 트레이드오프 고려 선택. `[v4.1]` — v4.1에서 추가/변경.

---

## 1. 시장 및 거래소 설정

|항목|값|비고|
|---|---|---|
|거래소|OKX||
|상품|BTC-USDT-SWAP|USDT 마진 무기한 선물|
|포지션 모드|양방향 (Hedge)|`posSide`: `long` 또는 `short`. 향후 동시 포지션 확장 대비 유지. `[v4.1 명시]`|
|마진 모드|교차 (Cross)|`tdMode: "cross"`. `[사용자 요청]`|
|계약 사양|ctVal=0.01 BTC, lotSz=1|시작 시 자동 조회 검증|

**OKX API 기본 정보**:

REST Base URL: `https://www.okx.com` (Demo: 동일 URL + 헤더 `x-simulated-trading: 1`) WebSocket Production: `wss://ws.okx.com:8443/ws/v5/public`, `wss://ws.okx.com:8443/ws/v5/private` WebSocket Demo: `wss://wspap.okx.com:8443/ws/v5/public`, `wss://wspap.okx.com:8443/ws/v5/private`

인증: `OK-ACCESS-KEY`, `OK-ACCESS-SIGN` (HMAC-SHA256, Base64), `OK-ACCESS-TIMESTAMP`, `OK-ACCESS-PASSPHRASE` 서명: `Base64(HMAC-SHA256(timestamp + method + requestPath + body, secretKey))`

---

## 2. 운영 단계 (Stages)

config.py 변수: `TRADING_STAGE = 1 | 2 | 3`

### Stage 1 — 자체 모의 (Paper Trading)

Python 내부에서 가상 포지션 관리. OKX에 실제 주문 없음. 가격은 OKX WebSocket 실시간 수신. 가상 수수료·슬리피지·펀딩비 시뮬레이션 (섹션 6). 서킷 브레이커 비활성.

### Stage 2 — OKX 데모

OKX 데모 API 사용. 실제 체결 로직 검증. 서킷 브레이커 비활성.

### Stage 3 — 실전 (Live)

실제 자금 거래. 서킷 브레이커 활성 (일일 누적 손실 15% 시 당일 매매 강제 중단). `[AI 합의]`

### Stage 전환 조건 `[AI 합의]`

다음 조건을 **모두** 충족해야 한다:

- 거래 횟수 ≥ 50회
- EV 양수
- MDD ≤ 20%
- 연속 운영 ≥ 60일
- `principles.json`에 최소 1개 리스크 관련 규칙 (Stage 2→3 시)

**전환 방식** `[v4.1]`: 조건 충족 시 Python이 텔레그램으로 알림. 사용자가 `/confirm_stage2` 또는 `/confirm_stage3` 명령어로 승인해야 전환. 자동 전환 없음.

---

## 3. 절대 규칙 (하드코딩 — Python이 강제)

|#|규칙|값|config.py 변수|
|---|---|---|---|
|1|SL 필수|예|—|
|2|1회 최대 손실|자본의 5%|`MAX_LOSS_PERCENT = 0.05` `[사용자 요청]`|
|3|동시 포지션|최대 1개|`MAX_CONCURRENT_POSITIONS = 1` `[사용자 요청]`|
|4|레버리지 상한|10×|`LEVERAGE = 10` `[사용자 요청]`|
|5|SL 불리하게 이동|금지|—|
|6|시장가 슬리피지 제한|0.5% (high vol 시 1.0%)|`MAX_ENTRY_SLIPPAGE = 0.005`, `MAX_ENTRY_SLIPPAGE_HIGH_VOL = 0.01` `[v4.1]`|

**TP 필수 여부**: 아니오. 트레일링 스톱 허용. `[AI 합의]`

**일일·주간 손실 한도**: Stage 1~2 하드코딩 없음. 단, 연속 3패 또는 일일 누적 손실 10% 초과 시 `immediate_review` 트리거 발동 (차단 아님, 점검 강제). `[v4.1]` Stage 3에서는 일일 15% 하드락 활성. `[사용자 요청 + AI 합의]`

---

## 4. 분석 체계

### 4-1. 타임프레임 및 캔들 수

|타임프레임|기본 캔들 수|업데이트 시|bar 값|용도|
|---|---|---|---|---|
|1D|200|20|`1D`|큰 그림, 추세|
|4H|200|30|`4H`|중기 구조|
|1H|200|30|`1H`|진입 타이밍|
|15m|50|50|`15m`|정밀 진입|

### 4-2. 지표 시스템

**기본 지표 (항상 활성)**:

|지표|파라미터|계산 방식|
|---|---|---|
|가격 구조|—|스윙 고점/저점 감지|
|RSI|period=14|Wilder's smoothing|
|볼륨 클러스터|—|최근 20캔들 대비 거래량 비율|
|피보나치 되돌림|—|0.236, 0.382, 0.5, 0.618, 0.786|
|EMA-20 / EMA-50|20, 50|지수이동평균|

**확장 지표 (CHLOE가 활성/비활성 결정)**:

|지표|파라미터|초기 상태|
|---|---|---|
|MACD|fast=12, slow=26, signal=9|비활성|
|볼린저 밴드|period=20, std=2|비활성|
|OBV|—|비활성|
|VWAP|—|비활성|
|ADX|period=14|활성|
|ATR|period=14|활성|

`analysis_routine.json` 구조:

```json
Copy{
  "indicators": [
    {"name": "RSI", "active": true, "params": {"period": 14}},
    {"name": "MACD", "active": false, "params": {"fast": 12, "slow": 26, "signal": 9}},
    {"name": "BB", "active": false, "params": {"period": 20, "std": 2}},
    {"name": "OBV", "active": false, "params": {}},
    {"name": "VWAP", "active": false, "params": {}},
    {"name": "ADX", "active": true, "params": {"period": 14}},
    {"name": "ATR", "active": true, "params": {"period": 14}}
  ],
  "version": "1.0",
  "last_updated_by": "CHLOE",
  "last_updated_at": ""
}
```

### 4-3. 시장 환경 태깅 (Python 자동)

|태그|계산|값|
|---|---|---|
|추세|EMA-20 vs EMA-50 + ADX(14)|`up`/`down`/`sideways`|
|변동성|ATR(14) / 20일 ATR 평균|`high`(>1.3)/`normal`(0.7~1.3)/`low`(<0.7)|
|펀딩 편향|OKX 펀딩비율|`long_bias`/`short_bias`/`neutral`|
|요일|UTC 기준|`weekday`/`weekend`|

### 4-4. trend_strength

`trend_strength = (EMA20 - EMA50) / EMA50 × 100`

### 4-5. by_indicator 라벨링 규칙 `[v4.1]`

`by_indicator` 통계의 키는 `active_indicators` + 진입 시점 지표 값 조건으로 자동 생성한다. 예: RSI < 30이면 `RSI_below_30`, EMA20 > EMA50이면 `EMA_cross_bullish`. indicators.py에서 정의하며 CHLOE가 `update_analysis_routine`으로 커스텀 라벨을 추가할 수 있다.

---

## 5. 매매 흐름

### 5-1. 전체 플로우

```
[시스템 시작 — main.py]
  1. cache/ 디렉터리 및 JSON 파일 무결성 확인
  2. system_state.json 확인 → 비정상이면 텔레그램 알림
  3. API 연결 테스트: OKX REST, Claude API, Google Sheets
  4. OKX 계약 사양 조회 (ctVal, lotSz)
  5. 레버리지 설정
  6. 비정상 종료였다면: OKX 체결 내역 조회 → CHLOE에게 보고
  7. WebSocket 연결 시작 (tickers, orders, positions)
  8. 텔레그램 봇 폴링 시작
  9. 첫 가격 피드 수신 → first_analysis 트리거

[메인 루프 — scheduler.py]
  a. WebSocket 실시간 가격 수신
  b. 알림 가격 도달 체크 (Python — Claude 호출 없음)
  c. 도달 시 → alert_triggered
  d. 포지션 종료 감지 → trade_closed
  e. 텔레그램 메시지 → user_message
  f. 비긴급 트리거 배칭 (2~3분 이내)
  g. 24시간 경과 시 → daily_stats_rebuild() → meta_check
  h. 하트비트: 1시간마다
  i. Sheets 동기화: 5분마다
  j. cold store 정리: 24시간마다
  k. 연속 3패 또는 일일 10% 손실 감지 → immediate_review [v4.1]

트리거 우선순위 [v4.1]:
  alert_triggered > immediate_review > trade_closed > user_message > periodic_review > meta_check
```

### 5-2. 알림 관리

알림에 고정 만료 시간 없음. CHLOE가 유효 조건을 함께 기록.

```json
Copy[
  {
    "id": "alert_001",
    "price": 68000,
    "direction": "above",
    "scenario_id": "scenario_001",
    "valid_condition": "4H 캔들이 67500 위에서 마감하는 한 유효",
    "created_at": "2026-02-21T09:00:00Z",
    "created_by": "CHLOE"
  }
]
```

### 5-3. 재분석 방지

이전 분석 결론을 `current_analysis.json`에서 읽어 함께 전달. 유효하면 "변동 없음"으로 끝냄.

### 5-4. 진입 방식

시장가(기본) 또는 지정가. 진입 도구 호출 시:

```json
Copy{
  "action": "open_long",
  "ordType": "market",
  "sl_price": 66500,
  "tp_price": 70000,
  "acceptable_price_range": {"min": 67800, "max": 68200},
  "reason": "1H 지지선 리테스트 후 반등 확인"
}
```

**지정가 주문 만료/취소** `[v4.1]`: scheduler.py가 미체결 지정가 주문을 추적. CHLOE가 `valid_for_minutes`(기본 60분)를 지정 가능. 만료 시 Python이 자동 취소 + CHLOE 알림.

**진입 전 검증 흐름 (executor.py)** `[v4.1 — 7단계]`:

```
[하드코딩 절대 규칙 — 위반 시 강제 차단]
1. SL 존재 확인
2. 포지션 크기 계산 → 손실 ≤ 5% 검증. 리스크 초과 시 진입 차단. [v4.1: 최소 1계약 강제 삭제]
3. 동시 포지션 수 ≤ 1
4. 레버리지 ≤ 10×
5. 슬리피지 검증 (acceptable_price_range + MAX_ENTRY_SLIPPAGE)
   변동성 태그 high일 때 MAX_ENTRY_SLIPPAGE_HIGH_VOL(1.0%) 적용 [v4.1]
6. Stage 3: 일일 누적 손실 15% 확인

[소프트 가드레일 — CHLOE 자체 원칙 매칭] [v4.1]
7. Python이 현재 env + action으로 principles.json 검색
   → 쿨오프 중인 원칙: 하드룰처럼 차단 (override 불가)
   → 매칭 없음: 그대로 진행
   → alert_level = "info": 실행 + 사후 알림
   → alert_level = "block_suggest": 보류 + CHLOE에게 확인 요청
     → override_principle_ids 포함 재호출 시 실행
   → 결과를 trigger_history에 기록
```

**가격 stale 처리** `[v4.1]`: 마지막 가격 수신 후 30초 이상 경과 시 stale 판정. stale 상태에서 시장가 진입 차단, CHLOE에게 "가격 데이터 지연으로 진입 보류" 반환.

**주문 폭주 방지** `[v4.1]`: 동일 방향 주문은 60초 이내 재시도 불가. 취소 재시도 최대 3회. 3회 실패 시 텔레그램 알림 후 해당 주문 포기.

### 5-5. 포지션 관리

진입 시 주문 JSON:

```json
Copy{
  "instId": "BTC-USDT-SWAP",
  "tdMode": "cross",
  "side": "buy",
  "posSide": "long",
  "ordType": "market",
  "sz": "3",
  "clOrdId": "chloe_20260221_091500_001",
  "attachAlgoOrds": [
    {
      "tpTriggerPx": "70000", "tpOrdPx": "-1",
      "slTriggerPx": "66500", "slOrdPx": "-1",
      "tpTriggerPxType": "last", "slTriggerPxType": "last"
    }
  ]
}
```

**clOrdId 형식** `[v4.1]`: `chloe_{YYYYMMDD}_{HHmmss}_{3자리 시퀀스}`. 일자 변경 시 시퀀스 리셋.

TP1 도달 시 50% 청산 + SL을 진입가로 이동. TP2 도달 시 나머지 청산. SL 변경: `amend-algos` API. 실패 시 `cancel-algos` + 재등록 폴백. SL 방향 제한: long은 새 SL ≥ 기존 SL. short은 새 SL ≤ 기존 SL.

### 5-6. 포지션 사이징 `[v4.1 수정]`

```python
Copymax_loss = balance * MAX_LOSS_PERCENT
stop_distance_btc = abs(entry_price - sl_price) * ctVal
contracts_raw = max_loss / stop_distance_btc
contracts = math.floor(contracts_raw / lotSz) * lotSz

# v4.1: 최소 1계약 강제 삭제. 리스크 초과 시 진입 차단.
if contracts < 1:
    # 진입 차단 + CHLOE에게 통보
    return "잔고 대비 SL 거리가 너무 넓어 리스크 5% 이내로 진입 불가. SL을 좁히거나 잔고를 늘려야 함"
```

### 5-7. SL 등록 실패 시 즉시 청산

진입 후 5초 이내 SL 상태 검증. live가 아니면 즉시 전량 시장가 청산 + 텔레그램 알림.

### 5-8. 시장가 슬리피지 검증

1. last_price 확인 (30초 초과 시 stale → 차단) `[v4.1]`
2. acceptable_price_range 확인
3. 변동성 normal 이하: MAX_ENTRY_SLIPPAGE(0.5%), high: MAX_ENTRY_SLIPPAGE_HIGH_VOL(1.0%) `[v4.1]`

---

## 6. 시뮬레이션 (Stage 1 전용)

|항목|값|
|---|---|
|가상 수수료|taker 0.05% 양방향|
|동적 슬리피지|`max((ATR/price) × 0.001, 0.0003)`|
|펀딩비|8시간마다 실제 OKX 조회|
|가상 포지션|`cache/paper_position.json`|

```json
Copy{
  "has_position": true,
  "side": "long",
  "entry_price": 68050.5,
  "size": 3,
  "sl_price": 66500,
  "tp_price": 70000,
  "entry_time": "2026-02-21T09:15:00Z",
  "entry_fee": 1.021,
  "funding_paid": 0.34,
  "unrealized_pnl": 25.6
}
```

---

## 7. 학습 시스템

### 7-1. 3계층 메모리

|계층|파일|CHLOE 읽기|
|---|---|---|
|원칙|`principles.json`|매 호출 시 전달|
|인사이트|`insights.json`|매 호출 시 **env 매칭은 전체, 나머지는 요약** `[v4.1]`|
|거래 메모|`trade_log.json` 내 `memo`|최근 10건|
|Cold Store|`cold_insights.json`|전달 안 함|

### 7-2. 통계 선행 → 인사이트 도출

```
거래 종료
  → CHLOE: 거래 메모 작성 (save_trade_memo)
  → Python: 자동 통계 계산
  → CHLOE: 인사이트 작성 (save_insight) — 단, alert_triggered 대기 시 alert 우선 처리 [v4.1]
  → Python: 인사이트 품질 검사 → 경고 태그 → tool_result 반환 [v4.1]
```

**performance.json**:

```json
Copy{
  "total_trades": 23,
  "win_rate": 0.478,
  "avg_rr": 1.85,
  "ev": 0.12,
  "max_drawdown": 0.087,
  "streak": {"current_win": 0, "current_loss": 2, "max_win": 5, "max_loss": 3},
  "by_environment": {
    "up_high": {"trades": 5, "win_rate": 0.80, "avg_rr": 2.1}
  },
  "by_indicator": {
    "RSI_below_30": {"trades": 8, "win_rate": 0.75, "avg_rr": 2.1}
  },
  "daily_pnl": -1.25,
  "weekly_pnl": 3.40,
  "last_rebuild": "2026-02-21T00:00:00Z",
  "learning_health": {}
}
```

통계 계산: 거래 종료 직후 즉시 + 24시간마다 전체 재계산(`daily_stats_rebuild()`).

### 7-3. 인사이트 관리 `[v4.1 확장]`

```json
Copy[
  {
    "id": "ins_001",
    "content": "RSI 30 이하에서 long 진입 시 승률 75%. sideways에서만 유효.",
    "confidence": "high",
    "sample_count": 8,
    "win_rate": 0.75,
    "created_at": "2026-02-21T12:00:00Z",
    "last_used_at": "2026-02-21T15:00:00Z",
    "category": "entry",
    "warnings": [],
    "trigger_conditions": {
      "env_match": {"trend": "sideways"},
      "action_match": ["open_long"],
      "indicator_match": {"RSI": {"below": 30}}
    },
    "reasoning_chain": {
      "origin_trades": ["trade_005", "trade_008", "trade_012", "trade_015"],
      "key_observation": "RSI<30 진입 4회 중 3회 성공. up+high 환경 실패 후 sideways 조건 추가.",
      "failed_attempts": [
        {
          "trade_id": "trade_015",
          "what_went_wrong": "up+high 환경에서 급락. SL 피격.",
          "lesson_applied": "sideways 환경 필터 추가"
        }
      ],
      "supersedes": null,
      "evolution_history": [
        {"date": "2026-02-15", "version": "v1", "content": "RSI 30 이하 long 유효"},
        {"date": "2026-02-21", "version": "v2", "content": "sideways 조건 추가"}
      ]
    }
  }
]
Copy
```

**인사이트 전달 — Pre-fetching** `[v4.1]`: Part C에서 현재 env와 `trigger_conditions`가 매칭되는 인사이트는 reasoning_chain 포함 전체 전달. 비매칭은 요약만 전달 (상위 10개, confidence + 최신순). `get_insight_detail`은 비긴급 상황에서만 사용.

**Confidence**: low(5~9회) / medium(10~19회) / high(20회+) **Cold Store**: 14일 미사용 시 자동 이동. 삭제 안 함. **과최적화 방지**: win_rate < 40%이면 confidence 다운그레이드.

**인사이트 아카이브/폐기** `[v4.1]`: `save_insight(action="invalidate")`로 폐기 사유 기록. `invalidated: true`로 표시. 유사 패턴 재생성 시 Python이 과거 폐기 이력 알림.

### 7-4. 원칙 관리 `[v4.1 확장]`

```json
Copy[
  {
    "id": "prin_001",
    "content": "sideways + high volatility에서 long 진입 자제",
    "based_on_insight_id": "ins_003",
    "created_at": "2026-02-21T12:00:00Z",
    "trigger_conditions": {
      "env_match": {"trend": "sideways", "volatility": "high"},
      "action_match": ["open_long"]
    },
    "alert_level": "block_suggest",
    "trigger_history": {
      "total_triggered": 3,
      "followed": 2,
      "ignored": 1,
      "ignored_outcomes": [
        {"trade_id": "trade_045", "result": "loss", "pnl_r": -1.2}
      ]
    },
    "cooloff_remaining": 0
  }
]
```

**쿨오프 규칙** `[v4.1]`: 원칙을 override한 거래가 -1.0R 이하로 종료되면, `alert_level`이 자동 `block_suggest`로 상향되고 `cooloff_remaining = 3`. 쿨오프 중에는 해당 원칙이 하드룰처럼 작동 (override 불가). 3회 트리거 경과 후 소프트 가드레일로 복귀.

### 7-5. 인사이트/원칙 품질 검사 `[v4.1]`

**인사이트 품질 검사 (5항목)**:

|#|검사|조건|동작|
|---|---|---|---|
|1|샘플 충분성|origin_trades < 3|confidence 강제 low + 경고|
|2|환경 편향|80% 이상 동일 환경|경고 태그|
|3|시간 편향|전부 최근 7일 이내|경고 태그|
|4|중복/모순|trigger_conditions 80% 이상 겹침|CHLOE에게 충돌 알림 `[v4.1]`|
|5|Confidence 조정|sample_count 기준|자동 조정|

**원칙 승격 검사**: sample_count < 10 또는 win_rate < 50%이면 경고 (강행 가능).

### 7-6. 학습 건강 보고서 `[v4.1]`

`performance.json["learning_health"]`에 저장. `daily_stats_rebuild()` 시 생성. `meta_check` 시 Part C에 포함.

```json
Copy{
  "learning_health": {
    "insight_usage": {"ins_001": {"referenced": 3, "last_7d": true}},
    "principle_compliance": {"prin_001": {"triggered": 2, "followed": 2, "ignored": 0}},
    "insight_accuracy_drift": {"ins_001": {"overall_wr": 0.75, "recent_5_wr": 0.60, "drift": -0.15}},
    "category_balance": {"entry": 8, "exit": 1, "risk": 1, "market": 0, "general": 0},
    "overfit_risk": {"low_sample_ratio": 0.6, "env_bias_ratio": 0.4},
    "regime_shift_warning": false,
    "generated_at": "2026-02-22T00:00:00Z"
  }
}
```

**regime_shift_warning** `[v4.1]`: 전체 승률 대비 최근 5회 승률이 -20%p 이상 하락한 인사이트가 3개 이상이면 `true`.

### 7-7. 사용자 초기 힌트

시스템 프롬프트 Part A에 포함:

> "횡보는 단순 수평이 아니라 내부 구조가 있다. RSI 흐름, 거래량 변화, 횡보의 기울기를 관찰. 돌파 후 횡보가 이어지면 진짜 돌파, 즉시 되돌림은 페이크아웃 가능성이 높다." → 힌트일 뿐. 직접 검증하고 발전시킬 것.

---

## 8. AI 호출 최적화

### 8-1. 모델

Claude Sonnet 4.5 — `claude-sonnet-4-5-20250929` — Input $3/MTok, Output $15/MTok. 단일 모델. `[사용자 요청]`

### 8-2. Prompt 구조 (3-Part 캐싱)

|Part|내용|cache_control|토큰|
|---|---|---|---|
|A|정체성 + 절대 규칙 + 학습 원칙 + 힌트|ephemeral|~900|
|B|분석 프레임워크 + 복기 + 품질 기준|ephemeral|~700|
|C|현재 상태 (동적)|없음|~700|

합계 ~2,300 토큰. Part A+B 캐시 히트 시 ~70% 절감. 배칭: 마지막 호출 후 2~3분 이내 비긴급 작업 실행.

### 8-3. 트리거별 설정

|트리거|조건|max_iterations|예외|
|---|---|---|---|
|`first_analysis`|시작 후 첫 가격|5|아니오|
|`alert_triggered`|알림 도달|4|**예**|
|`trade_closed`|포지션 종료|5|**예**|
|`periodic_review`|4시간마다, **활성 시나리오 또는 포지션 있을 때만** `[v4.1]`|3|아니오|
|`user_message`|텔레그램 메시지|3|아니오|
|`immediate_review`|연속 손실/큰 손실|3|**예**|
|`meta_check`|1일 1회|2|아니오|

**호출 제한**: 시간당 최대 **15회** `[v4.1]`. 예외 트리거는 제한 제외.

### 8-4. 비용 추정

|항목|수치|
|---|---|
|운영 시간|8~12시간/일|
|예상 호출|6~12회/일|
|월간 추정|**$15~35** `[v4.1]`|

---

## 9. 메모리 및 저장소

### 9-1. 로컬 캐시 파일 (12개)

|파일|초기값|
|---|---|
|`current_analysis.json`|`{}`|
|`scenarios.json`|`[]`|
|`paper_position.json`|`{"has_position": false}`|
|`trade_log.json`|`[]`|
|`insights.json`|`[]`|
|`cold_insights.json`|`[]`|
|`principles.json`|`[]`|
|`performance.json`|`{"total_trades":0, "last_rebuild":"", "learning_health":{}}`|
|`analysis_routine.json`|섹션 4-2 구조|
|`alerts.json`|`[]`|
|`daily_loss.json`|`{"daily":0, "weekly":0, "daily_reset":"", "weekly_reset":""}`|
|`system_state.json`|`{"shutdown":"unknown"}`|

**trade_log.json 레코드** `[v4.1 확장]`:

```json
Copy{
  "id": "trade_001",
  "side": "long",
  "entry_price": 68050,
  "exit_price": 69200,
  "size": 3,
  "sl_price": 66500,
  "tp_price": 70000,
  "entry_time": "2026-02-21T09:15:00Z",
  "exit_time": "2026-02-21T14:30:00Z",
  "pnl_usdt": 3.45,
  "pnl_r": 1.97,
  "fees": 0.68,
  "funding": 0.12,
  "slippage": 0.34,
  "env": {"trend": "up", "volatility": "normal", "funding": "neutral", "day": "weekday"},
  "memo": "1H 지지선 리테스트 후 반등.",
  "exit_reason": "tp1_hit",
  "active_indicators": ["RSI", "ADX", "ATR"],
  "principle_triggered": [
    {
      "principle_id": "prin_001",
      "alert_level": "block_suggest",
      "action_taken": "followed",
      "reason": ""
    }
  ]
}
```

백업: `.bak` 복사 후 덮어쓰기. 손상 시 `.bak` 복구. 둘 다 없으면 초기값 + 텔레그램 알림.

### 9-2. Google Sheets 백업

5분 동기화. gspread + Service Account. 6개 탭. 로컬 JSON이 source of truth.

---

## 10. 텔레그램 인터페이스

### 10-1. 자동 보고

진입, 청산, SL/TP 이동, 오류, 1시간 하트비트. 동일 에러 1분 내 중복 전송 안 함.

### 10-2. 사용자 → CHLOE

모든 메시지 원문 전달. CHLOE 자율 판단.

### 10-3. 빠른 명령어

|명령어|동작|
|---|---|
|`/status`|현재가, 포지션, 손익, 시나리오, 알림|
|`/summary`|PnL, 승률, EV, 거래 횟수|
|`/panic`|전량 청산 + 시스템 정지|
|`/pause`|새 진입 중단|
|`/resume`|pause/panic 해제|
|`/stop`|정상 종료|
|`/cost`|Claude 호출 횟수/비용|
|`/mute N`|N시간 알림 무음|
|`/confirm_stage2`|Stage 2 전환 승인 `[v4.1]`|
|`/confirm_stage3`|Stage 3 전환 승인 `[v4.1]`|

### 10-4. /pause · /panic 기록

```json
Copy{
  "paused": true,
  "pause_reason": "user_panic",
  "pause_time": "2026-02-21T14:30:00Z",
  "market_snapshot": {"price": 68500, "trend": "up", "volatility": "high"}
}
```

/resume 시 CHLOE에게 정지 기간·가격 정보 전달.

### 10-5. 포지션 소실 대처

Stage 2~3에서 30초마다 확인. 소실 시 텔레그램 질문 → 5분 대기 → 자동 OKX 이력 조회.

---

## 11. 시스템 프롬프트 전문

### Part A — 정체성 + 절대 규칙 + 학습 원칙

```
너는 CHLOE(클로이). 자율적으로 학습하고 성장하는 BTC 트레이더 AI.

[궁극적 목표]
스스로 분석, 매매, 복기, 학습하여 독립적 트레이더로 성장.
모든 판단과 기록은 자율적이다.

[핵심 철학]
- '언제'가 아니라 '왜'에 집중.
- 인사이트는 통계에서 도출. Python 통계를 먼저 확인.
- 기록의 형식과 내용은 자율 결정.

[절대 규칙 — 변경 불가]
1. SL 없이 진입 금지
2. 1회 손실 ≤ 자본 5%
3. 동시 포지션 최대 1개
4. 레버리지 ≤ 10×
5. SL 불리한 방향 이동 금지
6. 슬리피지 0.5% 초과 시 진입 차단 (high vol: 1.0%)
(Python이 강제. 리스크 초과 시 진입 자체가 차단됨.)

[학습 원칙] [v4.1]
- 인사이트 작성 시 근거 거래 ID, 핵심 관찰, 실패 경험 필수.
- 원칙 작성 시 트리거 조건(환경/행동) 필수.
- Python 경고 태그를 진지하게 검토.
- 자신이 세운 원칙이 트리거되면, 무시할 명확한 근거 없는 한 따른다.
  무시 시 반드시 근거를 거래 메모에 기록.

[재분석 판단]
이전 분석이 유효하면 "변동 없음"으로 끝낸다.

[사용자 초기 힌트]
"횡보는 내부 구조가 있다 — RSI, 거래량, 기울기 관찰.
돌파 후 횡보 = 진짜 돌파, 즉시 되돌림 = 페이크아웃."
→ 힌트일 뿐. 직접 검증하고 발전시킬 것.
```

### Part B — 분석·복기 + 학습 품질 기준

```
[분석 절차]
1. 1D → 4H → 1H/15m 순서
2. 시장 환경 태그 확인
3. 시나리오 도출 + 알림 설정

[진입 판단]
- 알림 도달 시 1H + 15m 재검증
- SL 필수, TP 선택
- acceptable_price_range 지정 가능

[포지션 관리]
- TP1: 50% 청산 + SL을 진입가로
- TP2: 나머지 청산
- 트레일링 스톱 가능

[복기 절차]
1. 거래 메모 (save_trade_memo)
2. Python 통계 확인
3. 인사이트 도출 (save_insight — 근거·관찰·실패 필수)
4. 필요 시 원칙 승격 (save_principle — 트리거 조건 필수)
5. conclude_review로 조기 종료 가능

[과최적화 방지]
- 샘플 5회 미만 인사이트에 높은 가중치 금지
- win_rate < 40%이면 confidence 하향 (Python 자동)
- 14일 미사용 → cold store (Python 자동)
- "느낌"으로 인사이트 만들지 않는다

[학습 품질 기준] [v4.1]
- 샘플 3건 미만: 인사이트 불가, 관찰 메모로만 기록
- 상세 맥락 필요 시 get_insight_detail 사용
- meta_check 시 학습 건강 보고서 검토
- 인사이트 폐기 시 save_insight(action="invalidate")로 사유 기록
```

### Part C — 동적 상태

```
[현재 상태]
현재가: {price} USDT | 24H: {change}%
환경: 추세={trend}, 변동성={volatility}, 펀딩={funding}, 요일={day}

[포지션]
{summary 또는 "없음"}

[잔고]
{balance} USDT (Stage {stage})

[시나리오] {scenarios 또는 "없음"}
[알림] {alerts 또는 "없음"}

[원칙]
{각 원칙 content + trigger_conditions + 최근 trigger_history}

[인사이트 — env 매칭 전체 + 나머지 요약 상위 10개] [v4.1]
{env 매칭: reasoning_chain 포함 전체}
{비매칭: id | category | confidence(sample) | win_rate | 한줄요약}
⚠️ = 경고 태그
→ 상세: get_insight_detail

[최근 10 거래] {요약}
[성과] 일간/주간 PnL, 승률, R:R, EV, 환경별

[학습 건강 보고서 — meta_check 시만] [v4.1]
활용도, 준수율, 정확도 변화, 카테고리 균형, 과최적화 위험, regime_shift_warning

[이전 분석] {결론 또는 "첫 분석"}
[트리거] {type}: {description}
```

---

## 12. Claude 도구 정의 (16개)

### 12-1. 분석·시나리오

**get_candles**: `{timeframe: enum, count: int}` required: timeframe, count

**save_analysis**: `{direction: enum, key_levels: {support, resistance}, summary: str, confidence: enum}` required: direction, summary

**save_scenario**: `{scenario_id, description, direction: enum, entry_zone, sl_price, tp_prices, valid_condition}` required: scenario_id, description, direction

**set_alert**: `{price, direction: enum, scenario_id, valid_condition}` required: price, direction

**remove_alert**: `{alert_id}` required: alert_id

### 12-2. 매매

**open_position** `[v4.1: override_principle_ids 추가]`:

```json
Copy{
  "name": "open_position",
  "input_schema": {
    "properties": {
      "action": {"enum": ["open_long", "open_short"]},
      "ordType": {"enum": ["market", "limit"], "default": "market"},
      "px": {"type": "number"},
      "sl_price": {"type": "number"},
      "tp_price": {"type": "number"},
      "acceptable_price_range": {"properties": {"min": {}, "max": {}}},
      "reason": {"type": "string"},
      "override_principle_ids": {"type": "array", "items": {"type": "string"}}
    },
    "required": ["action", "sl_price", "reason"]
  }
}
```

**close_position**: `{close_percent: 0-100, reason}` required: reason

**modify_sl**: `{new_sl_price, reason}` required: both

**modify_tp**: `{new_tp_price (0=제거), reason}` required: reason

### 12-3. 학습

**save_trade_memo**: `{trade_id, memo}` required: both

**save_insight** `[v4.1 확장]`:

```json
Copy{
  "name": "save_insight",
  "input_schema": {
    "properties": {
      "action": {"enum": ["create", "archive", "invalidate"], "default": "create"},
      "content": {"type": "string"},
      "category": {"enum": ["entry", "exit", "risk", "market", "general"]},
      "supporting_data": {"type": "string"},
      "origin_trades": {"type": "array"},
      "key_observation": {"type": "string"},
      "failed_attempts": {"type": "array"},
      "supersedes": {"type": "string"},
      "trigger_conditions": {"type": "object"},
      "insight_id": {"type": "string"},
      "reason": {"type": "string"}
    },
    "required": ["action"]
  }
}
```

_create 시 content, category, supporting_data, origin_trades, key_observation 필수. archive/invalidate 시 insight_id, reason 필수._

**save_principle** `[v4.1: trigger_conditions 필수]`:

```json
Copy{
  "name": "save_principle",
  "input_schema": {
    "properties": {
      "content": {"type": "string"},
      "based_on_insight_id": {"type": "string"},
      "trigger_conditions": {"type": "object"},
      "alert_level": {"enum": ["info", "block_suggest"], "default": "block_suggest"}
    },
    "required": ["content", "trigger_conditions"]
  }
}
```

**update_analysis_routine**: `{indicator, active, params}` required: indicator, active

**conclude_review**: `{summary}` required: summary

**send_telegram**: `{message}` required: message

### 12-4. 조회 `[v4.1 신규]`

**get_insight_detail**: `{insight_id}` required: insight_id ai_brain.py가 결과를 5분 인메모리 캐시에 보관. 동일 ID 재요청 시 캐시에서 즉시 반환. `[v4.1]`

---

## 13. 파일 구조 및 모듈 책임

```
trading_bot/
├── config.py / config_secret.py
├── data.py / indicators.py / tools.py
├── executor.py / ai_brain.py / logger.py
├── paper_trading.py / telegram_handler.py
├── websocket_client.py / scheduler.py / main.py
├── cache/ (12 JSON) / logs/
```

### 13-1. config.py

```python
Copy# === 거래 ===
SYMBOL = "BTC-USDT-SWAP"
LEVERAGE = 10
MAX_LOSS_PERCENT = 0.05
MAX_CONCURRENT_POSITIONS = 1
MAX_ENTRY_SLIPPAGE = 0.005
MAX_ENTRY_SLIPPAGE_HIGH_VOL = 0.01        # [v4.1]
TRADING_STAGE = 1

# === OKX URL ===
REST_BASE = "https://www.okx.com"
WS_PUBLIC = "wss://ws.okx.com:8443/ws/v5/public"
WS_PRIVATE = "wss://ws.okx.com:8443/ws/v5/private"
WS_PUBLIC_DEMO = "wss://wspap.okx.com:8443/ws/v5/public"
WS_PRIVATE_DEMO = "wss://wspap.okx.com:8443/ws/v5/private"

# === Stage 3 ===
STAGE3_DAILY_LOSS_HARD_LIMIT = 0.15

# === 타임프레임 ===
TIMEFRAMES = ["1D", "4H", "1H", "15m"]
CANDLE_COUNTS = {"1D": 200, "4H": 200, "1H": 200, "15m": 50}
UPDATE_COUNTS = {"1D": 20, "4H": 30, "1H": 30, "15m": 50}

# === AI ===
CLAUDE_MODEL = "claude-sonnet-4-5-20250929"
MAX_CLAUDE_CALLS_PER_HOUR = 15            # [v4.1: 10→15]
EXEMPT_TRIGGERS = ["alert_triggered", "trade_closed", "immediate_review"]
MAX_ITERATIONS = {
    "first_analysis": 5, "alert_triggered": 4, "trade_closed": 5,
    "periodic_review": 3, "user_message": 3, "immediate_review": 3, "meta_check": 2
}

# === 시뮬레이션 ===
SIM_TAKER_FEE = 0.0005
SIM_MIN_SLIPPAGE = 0.0003

# === 타이머 ===
SHEETS_SYNC_INTERVAL = 300
TELEGRAM_POLL_INTERVAL = 5
HEARTBEAT_INTERVAL = 3600
ALERT_THROTTLE_SECONDS = 60
COLD_STORE_DAYS = 14
STATS_REBUILD_INTERVAL = 86400
POSITION_CHECK_INTERVAL = 30
PRICE_STALE_SECONDS = 30                  # [v4.1]
ORDER_COOLDOWN_SECONDS = 60               # [v4.1]
MAX_CANCEL_RETRIES = 3                    # [v4.1]

# === 과최적화 ===
OVERFIT_FAILURE_THRESHOLD = 0.40
INSIGHT_CONFIDENCE = {"low": (5, 9), "medium": (10, 19), "high": (20, float("inf"))}

# === 학습 품질 [v4.1] ===
MIN_TRADES_FOR_INSIGHT = 3
ENV_BIAS_THRESHOLD = 0.8
TIME_BIAS_DAYS = 7
PRINCIPLE_MIN_SAMPLE = 10
PRINCIPLE_MIN_WINRATE = 0.5
COOLOFF_TRIGGER_COUNT = 3                 # [v4.1]
COOLOFF_LOSS_THRESHOLD = -1.0             # [v4.1] R 기준

# === 손실 점검 [v4.1] ===
CONSECUTIVE_LOSS_ALERT = 3
DAILY_LOSS_ALERT_PERCENT = 0.10

# === Stage 전환 ===
MIN_TRADES_FOR_TRANSITION = 50
MIN_DAYS_FOR_TRANSITION = 60
MAX_MDD_FOR_TRANSITION = 0.20
Copy
```

### 13-2. config_secret.py

```python
CopyOKX_API_KEY = ""
OKX_SECRET_KEY = ""
OKX_PASSPHRASE = ""
CLAUDE_API_KEY = ""
TELEGRAM_BOT_TOKEN = ""
TELEGRAM_CHAT_ID = ""
GOOGLE_SHEETS_CREDS_FILE = "credentials.json"
GOOGLE_SHEETS_SPREADSHEET_ID = ""
```

### 13-3. 모듈별 책임

**data.py**: OKX REST 래퍼. timeout 10초. 429/5xx 재시도 3회. `get_candles`, `get_ticker`, `get_balance`, `get_positions`, `get_funding_rate`, `get_instruments`, `place_order`, `cancel_order`, `cancel_algos`, `amend_algos`, `get_algo_orders_pending`, `set_leverage`

**indicators.py**: 지표 계산 + 환경 태깅 + trend_strength + by_indicator 라벨 생성 `[v4.1]`

**executor.py** `[v4.1 확장]`: 7단계 검증 (하드 6 + 소프트 1). 원칙 매칭 `_check_principle_match()`. 쿨오프 처리. override 기록. trigger_history 업데이트. 가격 stale 검증. 주문 폭주 방지 (60초 쿨다운, 취소 3회 제한). 슬리피지 동적 완화 (high vol 시 1.0%).

**ai_brain.py**: Claude API + 3-Part 프롬프트 + 도구 루프 + 호출 추적 + 비동기. get_insight_detail 5분 인메모리 캐시 `[v4.1]`. env 매칭 Pre-fetching으로 Part C 조립 `[v4.1]`.

**paper_trading.py**: Stage 1 가상 거래. 수수료·슬리피지·펀딩비 시뮬레이션.

**logger.py** `[v4.1 확장]`: JSON 캐시 + Sheets 동기화 + 인사이트 품질 검사(`validate_insight`) + 원칙 승격 검사(`validate_principle`) + 학습 건강 보고서 + regime_shift_warning + 인사이트 아카이브/폐기.

**telegram_handler.py**: 폴링 + 명령어 + `/confirm_stage2`, `/confirm_stage3` `[v4.1]` + pause/panic + throttling.

**websocket_client.py**: 3채널. 재연결 5회. REST 폴백 기본 30초, **포지션 보유 중 5초** `[v4.1]`.

**scheduler.py** `[v4.1 확장]`: 이벤트 루프. 트리거 우선순위 적용. periodic_review 활성 조건. 연속 패배/일일 손실 감지 → immediate_review. 지정가 주문 만료 추적. cold store 정리.

**main.py**: 9단계 시작 시퀀스. 계약 사양 → executor 전달. graceful shutdown. 크래시 시 텔레그램 + 로그 전송.

---

## 14. 종료 및 재시작

### 14-1. 정상 종료

포지션 있으면 경고. `system_state.json`에 `normal` 기록. WS 종료.

### 14-2. 비정상 종료

`normal` 기록 없으면 비정상. 크래시 시 텔레그램 + 로그 파일 전송 시도.

### 14-3. 재시작

비정상이면 알림 → 캐시 복구 → OKX 이력 조회 → CHLOE 보고 → 첫 분석.

### 14-4. 포지션 보호

SL/TP는 OKX algo order로 프로그램 꺼져도 실행. 트레일링/부분 청산은 실행 중 필요.

---

## 15. 오류 처리

|상황|처리|알림|
|---|---|---|
|REST 429/5xx|재시도 3회, 지수 백오프|3회 실패 시|
|WS 끊김|재연결 5회 → REST 폴백 (30초/포지션 시 5초) `[v4.1]`|실패 시|
|WS 재연결 후 불일치|REST 강제 동기화|즉시|
|Claude 실패|30초 대기 재시도 3회 → 건너뛰기|3회 실패 시|
|Sheets 실패|로컬 유지, 재시도|첫 실패|
|캐시 손상|.bak 복구, 없으면 초기값|즉시|
|SL 등록 실패|즉시 전량 청산|즉시|
|포지션 소실|질문 → 5분 대기 → 자동 조회|즉시|
|크래시|텔레그램 + 로그 전송|시도|
|가격 stale (30초+)|시장가 진입 차단 `[v4.1]`|CHLOE에게 알림|
|주문 취소 3회 실패|해당 주문 포기 `[v4.1]`|즉시|

---

## 16. 외부 하트비트

1시간마다 UptimeRobot HTTP 핑. 미수신 시 이메일/SMS 알림.

---

## 17. 개발 순서

|단계|파일|설명|
|---|---|---|
|1|config.py + config_secret.py|설정값 + 키|
|2|data.py|OKX REST|
|3|indicators.py|지표 + 태깅 + 라벨링|
|4|logger.py|캐시 + Sheets + 품질 검사|
|5|paper_trading.py|Stage 1 엔진|
|6|executor.py|7단계 검증 + 쿨오프|
|7|tools.py|16개 도구 스키마|
|8|ai_brain.py|Claude + 프롬프트 + Pre-fetching|
|9|telegram_handler.py|봇 + confirm 명령어|
|10|websocket_client.py|WS + 차등 폴백|
|11|scheduler.py|루프 + 우선순위 + 손실 감지|
|12|main.py|진입점|
|13|통합 테스트|전체 검증|

---

## 18. 현재 상태

|항목|값|
|---|---|
|잔고|~$34.93 USDT|
|Stage|1 (Paper Trading)|
|포지션 모드|양방향(Hedge) ✅|
|마진 모드|교차(Cross) ✅|
|상품|BTC-USDT-SWAP ✅|

---

## 19. 리뷰어 참고사항

하드코딩 규칙 6개 + 소프트 가드레일(원칙 매칭 + 쿨오프). 나머지 모든 판단은 CHLOE 자율.

1. CHLOE 자율성을 불필요하게 제한하는 항목이 있는가?
2. 비용 효율 개선 가능한 구조 변경이 있는가?
3. 하드룰에 빠진 안전장치가 있는가?
4. 통계 → 인사이트 흐름에 개선 가능한 부분이 있는가?
5. 실행 지연(3~10초)의 대처가 충분한가?
6. `[v4.1]` 학습 품질 검사가 자율성을 과도하게 제한하지 않는가?
7. `[v4.1]` Pre-fetching + 챕터링의 토큰 절약 효과가 충분한가?

---

_이 문서는 CHLOE AI Trading System의 설계·구현·코드 리뷰·수정 요청에 대한 유일한 기준 문서이다._