**문서 버전**: 4.0 (Final)  
**작성일**: 2026-02-21  
**용도**: 이 문서 하나만으로 어떤 AI 개발자든 동일한 시스템을 구현할 수 있어야 한다. 대화 맥락 없이 이 문서만 읽고 코드를 작성할 수 있는 수준의 상세함을 목표로 한다.  
**검토 이력**: o3 Pro, Grok, Gemini 3.1 Pro 검토 완료. 사용자 최종 승인 완료.

---
## 0. 프로젝트 철학 및 궁극적 목표

### 0-1. 궁극적 목표

이 시스템의 이름은 **CHLOE(클로이)**이다. CHLOE는 Anthropic Claude API를 기반으로 동작하는 자율형 BTC 선물 트레이딩 AI이다. CHLOE가 **스스로 분석하고, 스스로 매매하고, 스스로 복기하고, 스스로 학습**하여, 장기적으로 독립적인 트레이더로 성장하는 시스템을 만드는 것이 궁극적 목표이다.

### 0-2. 설계 원칙 (구현 시 모든 판단의 기준)

**원칙 1 — 자율성 우선**: CHLOE는 진입·청산·복기·학습을 자율 수행한다. 사용자는 텔레그램으로 관찰하고, 필요 시 대화로 개입한다. 사용자 확인(confirm)을 요구하지 않는다. CHLOE가 스스로 판단하고 실행한다.

**원칙 2 — "왜"에 집중**: CHLOE는 시간 의존 분석을 최소화한다. 다루는 것은 "언제"가 아니라 **"왜"**이다. 가격이 움직이는 이유, 구조가 바뀌는 이유에 집중한다. `[사용자 요청]`

**원칙 3 — 통계 기반 학습**: 인사이트는 느낌이 아닌 데이터에서 도출한다. 거래 메모 → Python 자동 통계 계산 → 통계 결과를 CHLOE에게 전달 → CHLOE가 데이터 기반 인사이트 작성. `[사용자 요청]`

**원칙 4 — 기록의 자율성**: CHLOE가 기록의 형식, 내용, 분류를 스스로 결정한다. 시스템은 저장 도구만 제공한다. `[사용자 요청]`

**원칙 5 — 비용 효율**: Python이 처리 가능한 작업(지표 계산, 가격 모니터링, 상태 조회)은 Python이 직접 수행하고, Claude는 "판단"이 필요한 시점에만 호출한다.

### 0-3. 태그 설명 (리뷰어용)

이 문서에서 사용하는 태그:

`[사용자 요청]` — 사용자가 직접 요구한 사항. 반박 가능하나 맥락 이해 필수.  
`[AI 합의]` — o3 Pro, Grok, Gemini 3.1 Pro 검토를 거쳐 확정된 사항.  
`[설계 결정]` — 트레이드오프를 고려한 현재 선택. 근거가 명시되어 있음.

---

## 1. 시장 및 거래소 설정

|항목|값|비고|
|---|---|---|
|거래소|OKX||
|상품|BTC-USDT-SWAP|USDT 마진 무기한 선물 (Perpetual Swap)|
|포지션 모드|양방향 (Hedge)|`posSide` 파라미터 사용: `long` 또는 `short`. 설정 완료.|
|마진 모드|교차 (Cross)|`tdMode: "cross"`. 설정 완료. `[사용자 요청]`|
|계약 사양|ctVal=0.01 BTC, lotSz=1|시작 시 `GET /api/v5/public/instruments` 로 자동 조회하여 검증|

**OKX API 기본 정보**:

REST Base URL (Production): `https://www.okx.com`  
REST Base URL (Demo): `https://www.okx.com` (헤더에 `x-simulated-trading: 1` 추가)  
WebSocket (Production): `wss://ws.okx.com:8443/ws/v5/public`, `wss://ws.okx.com:8443/ws/v5/private`  
WebSocket (Demo): `wss://wspap.okx.com:8443/ws/v5/public`, `wss://wspap.okx.com:8443/ws/v5/private`

인증 헤더: `OK-ACCESS-KEY`, `OK-ACCESS-SIGN` (HMAC-SHA256, Base64), `OK-ACCESS-TIMESTAMP` (ISO format), `OK-ACCESS-PASSPHRASE`  
서명 생성: `Base64(HMAC-SHA256(timestamp + method + requestPath + body, secretKey))`

---

## 2. 운영 단계 (Stages)

config.py 변수: `TRADING_STAGE = 1 | 2 | 3`

### Stage 1 — 자체 모의 (Paper Trading)

Python 내부에서 가상 포지션을 관리한다. OKX에 실제 주문을 보내지 않는다. 가격 데이터는 OKX WebSocket에서 실시간 수신한다. 가상 수수료, 슬리피지, 펀딩비를 시뮬레이션한다 (섹션 6 참조). 서킷 브레이커 비활성.

### Stage 2 — OKX 데모

OKX 데모 API를 사용한다 (REST 헤더에 `x-simulated-trading: 1`, WS는 demo URL). 실제 체결 로직을 검증하는 단계. 가상 잔고 사용. 서킷 브레이커 비활성.

### Stage 3 — 실전 (Live)

실제 자금으로 거래한다. 서킷 브레이커 활성 (일일 누적 손실 15% 도달 시 당일 모든 매매 강제 중단). `[AI 합의 — Gemini 제안, 사용자 승인]`

### Stage 전환 조건 `[AI 합의]`

다음 조건을 **모두** 충족해야 한다:

- 거래 횟수 ≥ 50회
- 기대값(EV) 양수 — `EV = (승률 × 평균 수익) - ((1-승률) × 평균 손실) > 0`
- 최대 손실폭(MDD) ≤ 20%
- 연속 운영 ≥ 60일
- `principles.json`에 최소 1개 리스크 관련 규칙 존재 (Stage 2→3 전환 시)

_승률·R:R 고정 기준은 삭제함 — 35% 승률 + 3.0 R:R 같은 트렌드 추종 전략을 허용하기 위함._

---

## 3. 절대 규칙 (하드코딩 — Python이 강제)

이 규칙들은 CHLOE가 변경할 수 없다. Python executor.py가 모든 주문 전에 강제 검증한다.

|#|규칙|값|config.py 변수|비고|
|---|---|---|---|---|
|1|SL(스톱로스) 필수|예|—|SL 없이 진입 시도 시 Python이 차단 `[AI 합의]`|
|2|1회 최대 손실|자본의 5%|`MAX_LOSS_PERCENT = 0.05`|`[사용자 요청]` 원래 2% → 사용자가 5%로 상향|
|3|동시 포지션|최대 1개|`MAX_CONCURRENT_POSITIONS = 1`|`[사용자 요청]` 절대 변경 불가|
|4|레버리지 상한|10×|`LEVERAGE = 10`|`[사용자 요청]`|
|5|SL 불리하게 이동|금지|—|진입가 방향으로만 이동 가능. Python이 검증.|
|6|시장가 슬리피지 제한|0.5%|`MAX_ENTRY_SLIPPAGE = 0.005`|주문 직전 현재 호가가 마지막 수신가 대비 0.5% 이상 괴리 시 진입 차단 `[AI 합의 — Gemini 제안]`|

**TP(익절) 필수 여부**: 아니오. TP는 선택 — 트레일링 스톱 전략 허용. `[AI 합의]`

**일일·주간 손실 한도**: Stage 1~2에서는 하드코딩하지 않는다. CHLOE가 누적 손실 데이터를 보고 스스로 거래 중단 여부를 판단한다. 이 판단 자체가 학습 대상이다. Stage 3에서는 일일 15% 하드락이 활성화된다. `[사용자 요청 + Gemini 합의]`

---

## 4. 분석 체계

### 4-1. 타임프레임 및 캔들 수

|타임프레임|기본 캔들 수 (첫 분석)|업데이트 시 캔들 수|OKX API bar 값|용도|
|---|---|---|---|---|
|1D|200|20|`1D`|큰 그림, 추세|
|4H|200|30|`4H`|중기 구조|
|1H|200|30|`1H`|진입 타이밍|
|15m|50|50|`15m`|알림 도달 시 정밀 진입|

OKX 캔들 API: `GET /api/v5/market/candles?instId=BTC-USDT-SWAP&bar={bar}&limit={limit}`  
과거 데이터: `GET /api/v5/market/history-candles` (동일 파라미터)

### 4-2. 지표 시스템

**기본 지표 (항상 활성 — indicators.py에서 항상 계산)**:

|지표|기본 파라미터|계산 방식|
|---|---|---|
|가격 구조|—|스윙 고점/저점 감지 (최근 N캔들 기준)|
|RSI|period=14|Wilder's smoothing|
|볼륨 클러스터|—|최근 20캔들 대비 거래량 비율|
|피보나치 되돌림|—|최근 스윙 고점-저점 기준 0.236, 0.382, 0.5, 0.618, 0.786|
|EMA-20 / EMA-50|20, 50|지수이동평균|

**확장 지표 (CHLOE가 활성/비활성 결정 — analysis_routine.json에서 관리)**:

|지표|기본 파라미터|초기 상태|
|---|---|---|
|MACD|fast=12, slow=26, signal=9|비활성|
|볼린저 밴드|period=20, std=2|비활성|
|OBV|—|비활성|
|VWAP|—|비활성|
|ADX|period=14|활성 (시장 환경 태깅에 사용)|
|ATR|period=14|활성 (슬리피지·변동성 계산에 사용)|

**동적 파라미터 변경**: CHLOE는 `update_analysis_routine` 도구를 통해 지표의 기간, 활성 여부, 파라미터를 JSON 형태로 변경할 수 있다. `[AI 합의]`

`analysis_routine.json` 구조 예시:

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

### 4-3. 시장 환경 태깅 (Python 자동 계산 — indicators.py)

Python이 계산하여 CHLOE에게 전달하는 태그:

|태그|계산 방식|값|
|---|---|---|
|추세|EMA-20 vs EMA-50 위치 + ADX(14)|`up` (EMA20>EMA50, ADX>25), `down` (EMA20<EMA50, ADX>25), `sideways` (ADX≤25)|
|변동성|ATR(14) / 20일 ATR 평균|`high` (>1.3), `normal` (0.7~1.3), `low` (<0.7)|
|펀딩 편향|OKX 펀딩비율|`long_bias` (>+0.03%), `short_bias` (<-0.03%), `neutral`|
|요일|UTC 기준|`weekday` / `weekend`|

### 4-4. trend_strength 정의

`trend_strength = (EMA20 - EMA50) / EMA50 × 100` (이격도 퍼센트)

양수면 상승 추세 강도, 음수면 하락 추세 강도. CHLOE가 복기를 통해 다른 계산법으로 변경 가능 (`update_analysis_routine` 도구 사용).

---

## 5. 매매 흐름

### 5-1. 전체 플로우 (상세)

```
[시스템 시작 — main.py]
  1. cache/ 디렉터리 및 JSON 파일 무결성 확인
     → 없으면 기본 구조 생성 (analysis_routine.json에 기본 지표 포함)
     → 손상 시 .bak에서 복구 시도
  2. system_state.json 확인
     → "normal"이 아니면 → 비정상 종료로 판단 → 텔레그램 알림
  3. API 연결 테스트: OKX REST, Claude API, Google Sheets
     → 실패 시 텔레그램 알림 + 재시도
  4. OKX에서 계약 사양 조회 (ctVal, lotSz 확인)
  5. OKX에서 레버리지 설정: POST /api/v5/account/set-leverage
     {"instId":"BTC-USDT-SWAP","lever":"10","mgnMode":"cross"}
  6. 비정상 종료였다면: OKX에서 마지막 종료 이후 체결 내역 조회
     → CHLOE에게 "부재 중 발생한 이벤트" 보고
  7. WebSocket 연결 시작 (tickers, orders, positions 채널)
  8. 텔레그램 봇 폴링 시작
  9. 첫 가격 피드 수신 → [첫 분석 트리거]

[첫 분석 트리거 — first_analysis]
  → data.py: 1D(200) + 4H(200) + 1H(200) 캔들 수집
  → indicators.py: 지표 계산 + 시장 환경 태깅
  → ai_brain.py: CHLOE 호출 (max_iterations=5)
  → CHLOE: 차트 분석 → 시나리오 생성 → 알림 가격 설정
  → 결과를 cache/에 저장 + Sheets 동기화

[메인 루프 — scheduler.py]
  반복:
    a. WebSocket에서 실시간 가격 수신
    b. 알림 가격 도달 여부 체크 (Python — Claude 호출 없음)
    c. 도달 시 → [알림 트리거]
    d. 포지션 종료 감지 시 → [거래 종료 트리거]
    e. 사용자 텔레그램 메시지 수신 시 → [사용자 메시지 트리거]
    f. 마지막 Claude 호출 후 2~3분 이내에 대기 중인 비긴급 작업 실행
       (periodic_review, meta_check — 캐시 히트율 극대화)
    g. 마지막 통계 재계산 후 24시간 경과 시 → daily_stats_rebuild()
       → 완료 직후 meta_check 트리거
    h. 하트비트: 1시간마다 텔레그램에 "시스템 정상 가동" 전송
    i. Sheets 동기화: 5분마다
    j. cold store 정리: 24시간마다

[알림 트리거 — alert_triggered]
  → data.py: 1H(30) + 15m(50) 캔들 수집
  → indicators.py: 지표 계산
  → ai_brain.py: CHLOE 호출 (max_iterations=4)
  → CHLOE: 현재 시장 상태 재검증 → 진입 판단
    → 진입 결정 시: executor.py가 규칙 검증 → 주문 실행
    → 미진입 시: 이유 기록 + 알림 재설정 또는 폐기

[거래 종료 트리거 — trade_closed]
  → Python: 통계 자동 계산 (승률, R:R, 환경별 성과 등)
  → ai_brain.py: CHLOE 호출 (max_iterations=5)
  → CHLOE: 복기 + 거래 메모 작성 + (선택적) 인사이트 도출

[사용자 메시지 트리거 — user_message]
  → 사용자 텔레그램 메시지 원문을 CHLOE에게 전달
  → ai_brain.py: CHLOE 호출 (max_iterations=3)
  → CHLOE: 자율 판단 (응답, 기록, 무시, 분석에 활용 등)

[메타 인지 트리거 — meta_check] (1일 1회)
  → Python: "현재 설정 검토 필요?" 질문
  → ai_brain.py: CHLOE 호출 (max_iterations=2)
  → CHLOE: 현재 analysis_routine, 원칙, 인사이트 검토 → 필요 시 조정
```

### 5-2. 알림 관리 `[AI 합의]`

알림에 **고정 만료 시간을 두지 않는다.** CHLOE가 알림 설정 시 "이 알림이 유효한 조건"을 함께 기록한다.

`alerts.json` 구조:

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

알림 도달 시 CHLOE가 현재 시장 상태를 보고 진입할지 무시할지 스스로 판단한다. **조건 기반이지 시간 기반이 아니다.**

### 5-3. 재분석 방지 `[설계 결정]`

재분석 시 이전 분석의 핵심 결론(방향, 주요 지지/저항, 시나리오)을 `current_analysis.json`에서 읽어 함께 전달한다. 시스템 프롬프트에 명시: "이전 분석 결과를 받으면, 먼저 '여전히 유효한가'를 판단한다. 유효하면 '변동 없음'으로 끝내고 새 분석을 하지 않는다."

### 5-4. 진입 방식

**기본**: 시장가(market order) 즉시 진입.  
**선택**: 지정가(limit order) — CHLOE가 "이 가격까지 내려오면 진입"을 판단할 수 있다.

CHLOE가 진입 도구 호출 시 파라미터:

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

- `ordType`: `"market"` 또는 `"limit"` (limit일 경우 `px` 파라미터 추가)
- `sl_price`: 필수. 없으면 Python이 차단.
- `tp_price`: 선택. 없어도 진입 가능.
- `acceptable_price_range`: 선택. CHLOE가 허용하는 진입 가격 범위. 미지정 시 하드코딩 슬리피지 제한(0.5%)만 적용. `[AI 합의 — Gemini 제안]`

### 5-5. 포지션 관리

**진입 시 주문 JSON (OKX REST)**:

```json
Copy{
  "instId": "BTC-USDT-SWAP",
  "tdMode": "cross",
  "side": "buy",
  "posSide": "long",
  "ordType": "market",
  "sz": "3",
  "clOrdId": "chloe_20260221_001",
  "attachAlgoOrds": [
    {
      "tpTriggerPx": "70000",
      "tpOrdPx": "-1",
      "slTriggerPx": "66500",
      "slOrdPx": "-1",
      "tpTriggerPxType": "last",
      "slTriggerPxType": "last"
    }
  ]
}
```

- `tpOrdPx: "-1"` = 시장가 청산
- `slOrdPx: "-1"` = 시장가 청산
- `clOrdId`: 고유 ID — 중복 주문 방지

**TP1 도달 시**: 50% 시장가 청산, SL을 진입가로 이동 (손익분기).  
**TP2 도달 시**: 나머지 전량 청산.  
**SL 변경**: OKX `POST /api/v5/trade/amend-algos` API로 기존 SL 가격 수정. 실패 시 `POST /api/v5/trade/cancel-algos` + 재등록 폴백. `[AI 합의]`

**SL 변경 요청 JSON**:

```json
Copy{
  "instId": "BTC-USDT-SWAP",
  "algoId": "448965992920907776",
  "newSlTriggerPx": "67500"
}
```

**SL 방향 제한**: Python executor.py가 검증. `long` 포지션이면 새 SL ≥ 기존 SL만 허용. `short` 포지션이면 새 SL ≤ 기존 SL만 허용.

### 5-6. 포지션 사이징

```python
Copy# ctVal과 lotSz는 시작 시 GET /api/v5/public/instruments에서 조회
# BTC-USDT-SWAP 기준: ctVal = 0.01 (BTC), lotSz = 1

max_loss = balance * MAX_LOSS_PERCENT  # 예: 34.93 * 0.05 = 1.7465 USDT
stop_distance_btc = abs(entry_price - sl_price) * ctVal  # 예: |68000-66500| * 0.01 = 15 USDT/contract
contracts_raw = max_loss / stop_distance_btc  # 예: 1.7465 / 15 = 0.1164
contracts = math.floor(contracts_raw / lotSz) * lotSz  # 예: floor(0.1164) * 1 = 0 → 최소 1로 설정

# 최소 1 계약 보장 (1 계약 손실이 max_loss 초과 시 경고)
if contracts < 1:
    contracts = 1
    actual_risk = stop_distance_btc / balance  # 실제 리스크 계산
    if actual_risk > MAX_LOSS_PERCENT:
        # 경고 로그 + CHLOE에게 통보
        log("WARNING: 1 contract risk {actual_risk:.1%} exceeds {MAX_LOSS_PERCENT:.1%}")
```

**중요**: Python이 포지션 크기를 리스크 한도에 맞춰 조정한 경우, 다음 CHLOE 호출 시 "요청한 X 계약 → 리스크 5% 초과로 Y 계약으로 축소됨" 또는 "최소 1 계약이지만 실제 리스크 X%로 초과" 를 명시적으로 알려준다. CHLOE는 이 정보를 학습에 활용한다.

### 5-7. SL 등록 실패 시 즉시 청산 `[AI 합의 — o3 Pro 제안]`

진입 주문 체결 확인 후 5초 이내에 `attachAlgoOrds`로 등록한 SL의 상태를 검증한다. OKX WebSocket `orders` 채널에서 algo order 상태를 확인하거나, REST `GET /api/v5/trade/orders-algo-pending`으로 조회한다.

SL이 `live` 상태가 아니면:

1. 즉시 포지션 전량 시장가 청산
2. 텔레그램 알림: "SL 등록 실패로 강제 청산"
3. CHLOE에게 사후 보고

### 5-8. 시장가 슬리피지 검증 `[AI 합의 — Gemini 제안]`

executor.py에서 시장가 주문 전 검증 순서:

1. 현재 WebSocket에서 마지막 수신한 가격(last_price) 확인
2. CHLOE가 `acceptable_price_range`를 지정했다면:
    - 현재가가 범위 밖이면 → 진입 취소 + CHLOE에게 "가격 범위 초과로 진입 취소됨. 현재가 X"
3. `acceptable_price_range`가 없더라도:
    - CHLOE가 판단 시점의 가격과 현재 last_price 간 괴리가 `MAX_ENTRY_SLIPPAGE`(0.5%) 초과 시 → 진입 차단 + 텔레그램 알림

---

## 6. 시뮬레이션 (Stage 1 전용)

|항목|값|비고|
|---|---|---|
|가상 수수료|taker 0.05%|매매 양방향 적용|
|동적 슬리피지|`max((ATR/price) × 0.001, 0.0003)`|진입·청산 모두 적용|
|펀딩비|8시간마다 실제 OKX 펀딩비율 조회|`GET /api/v5/public/funding-rate?instId=BTC-USDT-SWAP`|
|가상 포지션 저장|`cache/paper_position.json`||
|체결 가격|알림 도달 시점 가격 ± 슬리피지||

`paper_position.json` 구조:

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

|계층|파일|내용|CHLOE 읽기|
|---|---|---|---|
|원칙 (Principles)|`principles.json`|핵심 트레이딩 원칙. 통계 검증을 거친 것만.|매 호출 시 전달|
|인사이트 (Insights)|`insights.json`|패턴 인사이트. confidence level 포함.|매 호출 시 전달|
|거래 메모 (Trade Memos)|`trade_log.json` 내 `memo` 필드|개별 거래 자유형 메모. 원본 보존.|최근 10건만 전달|
|Cold Store|`cold_insights.json`|14일 미사용 인사이트 보관. 삭제하지 않음.|전달 안 함|

### 7-2. 통계 선행 → 인사이트 도출 `[사용자 요청]`

```
거래 종료
  → CHLOE: 자유형 거래 메모 작성 (save_trade_memo 도구 사용)
  → Python: 자동 통계 계산 (아래 항목)
  → 통계 결과를 CHLOE 호출 시 Part C에 포함
  → CHLOE: 데이터 기반 인사이트 작성 (save_insight 도구 사용)
```

**Python이 자동 계산하는 통계 (performance.json)**:

```json
Copy{
  "total_trades": 23,
  "win_rate": 0.478,
  "avg_rr": 1.85,
  "ev": 0.12,
  "max_drawdown": 0.087,
  "streak": {"current_win": 0, "current_loss": 2, "max_win": 5, "max_loss": 3},
  "by_environment": {
    "up_high": {"trades": 5, "win_rate": 0.80, "avg_rr": 2.1},
    "sideways_normal": {"trades": 10, "win_rate": 0.40, "avg_rr": 1.5},
    "down_high": {"trades": 3, "win_rate": 0.33, "avg_rr": 1.2}
  },
  "by_indicator": {
    "RSI_below_30_entry": {"trades": 8, "win_rate": 0.75, "avg_rr": 2.1},
    "EMA_cross_entry": {"trades": 6, "win_rate": 0.33, "avg_rr": 1.3}
  },
  "daily_pnl": -1.25,
  "weekly_pnl": 3.40,
  "last_rebuild": "2026-02-21T00:00:00Z"
}
```

**통계 계산 주기** `[AI 합의 — o3 Pro 제안]`:

- 거래 종료 직후: 해당 거래 반영한 즉시 업데이트
- 24시간마다 전체 재계산: `daily_stats_rebuild()` — 마지막 재계산 후 24시간 경과 시 다음 가격 피드에서 실행

인사이트는 기존 것을 수정하는 것이 아니라 **새로 작성**한다. **원본 메모는 절대 변형하지 않는다.**

### 7-3. 인사이트 관리

`insights.json` 구조:

```json
Copy[
  {
    "id": "ins_001",
    "content": "RSI 30 이하에서 long 진입 시 승률 75%. 단, sideways 환경에서만 유효.",
    "confidence": "high",
    "sample_count": 8,
    "win_rate": 0.75,
    "created_at": "2026-02-21T12:00:00Z",
    "last_used_at": "2026-02-21T15:00:00Z",
    "category": "entry"
  }
]
```

**Confidence Level**: `low`(5~9회) / `medium`(10~19회) / `high`(20회+) — 샘플 수 기반  
**카테고리 상한**: 소프트 가이드라인으로만 운용 (하드 제한 없음). CHLOE가 10개 초과 시 스스로 정리 판단. `[AI 합의]`  
**Cold Store**: 14일간 미사용(`last_used_at` 기준) 인사이트는 자동으로 `cold_insights.json`으로 이동. 삭제하지 않음. `[AI 합의]`  
**과최적화 방지**: 인사이트별 `win_rate`가 40% 미만이면 confidence 자동 다운그레이드.

### 7-4. 사용자 초기 힌트 `[사용자 요청]`

시스템 프롬프트 Part A에 포함. "이것은 힌트일 뿐이며 CHLOE가 직접 검증하고 발전시켜야 한다."

> "횡보는 단순 수평이 아니라 내부 구조가 있다. RSI 흐름, 거래량 변화, 횡보의 기울기(수평/상승/하강)를 관찰해야 한다. 지지·저항 돌파 후 횡보(consolidation)가 이어지면 진짜 돌파일 확률이 높고, 즉시 되돌림은 페이크아웃 가능성이 높다."

사용자가 텔레그램으로 추가 힌트를 보내면, CHLOE가 스스로 판단하여 기록하거나 무시한다.

---

## 8. AI 호출 최적화

### 8-1. 모델

**모델**: Claude Sonnet 4.5  
**API model string**: `claude-sonnet-4-5-20250929`  
**가격**: Input $3 / MTok, Output $15 / MTok

구현 시점에 Anthropic API 문서에서 최신 안정 모델 문자열을 확인할 것. config.py에 주석으로 "구현 시 최신 모델 확인" 표기.

단일 모델 사용. Haiku 분리 없음. `[사용자 요청 — "하이쿠는 빼는 걸로"]`

### 8-2. Prompt 구조 (3-Part 캐싱)

Anthropic의 prompt caching은 `cache_control: {"type": "ephemeral"}` 블록을 사용한다. TTL은 5분이며 서버 측에서 관리된다.

|Part|위치|내용|cache_control|예상 토큰|
|---|---|---|---|---|
|Part A|system message 첫 번째 블록|CHLOE 정체성 + 절대 규칙 + 사용자 힌트|`{"type": "ephemeral"}`|~800|
|Part B|system message 두 번째 블록|분석 프레임워크 + 복기 절차 + 과최적화 방지|`{"type": "ephemeral"}`|~600|
|Part C|user message|현재 상태 (가격, 포지션, 시나리오, 원칙, 성과 통계 등)|캐싱 없음|~600|

**합계**: ~2,000 토큰/호출 목표. Part A+B 캐시 히트 시 입력 비용 약 70% 절감.

**Batching으로 캐시 히트율 극대화** `[AI 합의 — Gemini 제안]`:  
periodic_review와 meta_check는 독립 타이머가 아니라, **마지막 Claude 호출 후 2~3분 이내**에 대기 중인 비긴급 작업으로 실행한다. 이렇게 하면 이미 캐싱된 Part A+B를 재활용할 수 있다. 최근 5분 내 호출이 없었다면 단독 실행.

### 8-3. 트리거별 설정

|트리거|실행 조건|수집 데이터|max_iterations|호출 제한 예외|
|---|---|---|---|---|
|`first_analysis`|시스템 시작 후 첫 가격 피드|1D+4H+1H 전체|5|아니오|
|`alert_triggered`|알림 가격 도달|1H(30)+15m(50)|4|**예**|
|`trade_closed`|포지션 종료 감지|포지션 결과 + 통계|5|**예**|
|`periodic_review`|4시간마다 (배칭)|요약만|3 (조기 종료 가능)|아니오|
|`user_message`|텔레그램 메시지|없음 (텍스트만)|3|아니오|
|`immediate_review`|연속 손실/큰 손실|포지션 + 최근 거래|3|**예**|
|`meta_check`|1일 1회 (배칭)|현재 설정 요약|2|아니오|

**호출 제한**: 시간당 최대 10회. 예외 트리거(`alert_triggered`, `trade_closed`, `immediate_review`)는 제한에서 제외.

**복기 조기 종료**: CHLOE가 `conclude_review` 도구를 호출하면 남은 iteration을 건너뛴다. `[AI 합의]`

**토큰 절감**: 최근 20 캔들 원시 데이터, 최근 10 거래 요약, 활성 인사이트·원칙만 전송.

### 8-4. 비용 추정

|항목|수치|
|---|---|
|운영 시간|8~12시간/일 (사용자 PC 가동 시간)|
|예상 호출|6~10회/일 (평균)|
|1회 비용 (단순)|입력 ~3,000 tok × $3/MTok + 출력 ~1,000 tok × $15/MTok ≈ $0.024|
|1회 비용 (도구 루프)|max_iterations 5 기준 ≈ $0.10|
|월간 추정|**$10~25** (거래 빈도에 따라 변동)|

**주의**: max_iterations가 비용의 핵심 드라이버. 조기 종료(`conclude_review`)로 절감.

---

## 9. 메모리 및 저장소

### 9-1. 로컬 캐시 파일 (`cache/` 디렉터리)

|파일|용도|초기값|
|---|---|---|
|`current_analysis.json`|최신 시장 분석 결과|`{}`|
|`scenarios.json`|활성 시나리오|`[]`|
|`paper_position.json`|Stage 1 가상 포지션|`{"has_position": false}`|
|`trade_log.json`|전체 거래 기록|`[]`|
|`insights.json`|활성 인사이트|`[]`|
|`cold_insights.json`|14일 미사용 인사이트 보관|`[]`|
|`principles.json`|CHLOE 트레이딩 원칙|`[]`|
|`performance.json`|누적 성과 통계|`{"total_trades":0, "last_rebuild":""}`|
|`analysis_routine.json`|지표 설정|섹션 4-2의 기본 구조|
|`alerts.json`|활성 가격 알림|`[]`|
|`daily_loss.json`|당일/주간 누적 손익|`{"daily":0, "weekly":0, "daily_reset":"", "weekly_reset":""}`|
|`system_state.json`|정상/비정상 종료 플래그|`{"shutdown":"unknown"}`|

**trade_log.json 레코드 구조**:

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
  "memo": "1H 지지선 리테스트 후 반등. 볼륨 확인. RSI 35에서 진입.",
  "exit_reason": "tp1_hit"
}
```

**백업**: 모든 JSON 저장 시 기존 파일을 `.bak`으로 복사 후 덮어쓰기. `[AI 합의]`

**시작 시 초기화**: `main.py` 시작 시 `cache/` 디렉터리와 모든 JSON 파일 존재 확인. 없으면 위의 초기값으로 생성. 손상 시(JSON 파싱 실패) `.bak`에서 복구. `.bak`도 없으면 초기값으로 재생성 + 텔레그램 알림.

### 9-2. Google Sheets 백업

**동기화 간격**: 5분  
**라이브러리**: `gspread` + Google Service Account  
**시트 탭**:

|탭 이름|내용|원본 JSON|
|---|---|---|
|CHLOE_차트분석|최신 분석 결과|`current_analysis.json`|
|CHLOE_시나리오|활성 시나리오|`scenarios.json`|
|CHLOE_인사이트|활성 인사이트|`insights.json`|
|CHLOE_원칙|트레이딩 원칙|`principles.json`|
|CHLOE_매매기록|전체 거래 기록|`trade_log.json`|
|CHLOE_성과|성과 통계|`performance.json`|

**역할**: 백업 및 사용자 열람용. 실시간 데이터는 로컬 JSON이 **source of truth**.  
**Sheets 실패 시**: 로컬 JSON은 유지. 연결 복구 시 자동 재시도. 텔레그램 알림.

---

## 10. 텔레그램 인터페이스

### 10-1. 자동 보고 (CHLOE → 사용자)

다음 이벤트 발생 시 텔레그램으로 즉시 보고:

- 포지션 진입 (방향, 가격, 수량, SL, TP)
- 포지션 청산 (가격, 손익)
- SL/TP 이동
- 오류/비정상 상황
- 1시간 하트비트 ("시스템 정상 가동")

**알림 throttling**: 동일 에러 메시지는 1분 내 중복 전송하지 않는다. `[AI 합의]`

### 10-2. 사용자 → CHLOE

사용자가 보내는 모든 일반 메시지는 CHLOE에게 원문 그대로 전달된다. CHLOE가 자율적으로 판단하여 응답, 기록, 무시, 분석에 활용한다. `[사용자 요청]`

사용자가 직접 행동을 요청하면(예: "포지션 닫아", "SL 바꿔"), CHLOE가 평가한 후 동의하면 실행하고, 반대 의견이 있으면 근거를 제시한다. 최종적으로는 사용자 요청을 존중한다.

### 10-3. 빠른 명령어 (Claude 호출 없음 — Python 직접 처리)

|명령어|동작|Claude 호출|
|---|---|---|
|`/status`|현재가, 포지션 미실현 손익, 오늘/주간 손익, 활성 시나리오, 다음 알림 가격, 마지막 분석 시각|없음|
|`/summary`|일간/주간 PnL, 승률, EV, 총 거래 횟수 텍스트 요약|없음|
|`/panic`|모든 포지션 시장가 청산 + 시스템 일시정지 (새 진입 차단)|없음|
|`/pause`|새 진입 중단, 기존 포지션 유지, 알림 계속|없음|
|`/resume`|pause/panic 해제|없음|
|`/stop`|정상 종료 (포지션 열림 시 경고)|없음|
|`/cost`|오늘/이번 달 Claude 호출 횟수 및 추정 비용|없음|
|`/mute N`|N시간 동안 일반 알림 무음 (에러·SL 체결은 전송)|없음|

### 10-4. /pause · /panic 휴지 정보 기록 `[AI 합의 — o3 Pro 제안]`

/pause 또는 /panic 실행 시:

```json
Copy// system_state.json에 추가
{
  "paused": true,
  "pause_reason": "user_panic",
  "pause_time": "2026-02-21T14:30:00Z",
  "market_snapshot": {"price": 68500, "trend": "up", "volatility": "high"}
}
```

/resume 실행 시:

- `paused` → `false`
- CHLOE에게 "X시간 동안 정지 상태였음. 정지 시점 가격 Y, 현재 가격 Z" 전달

### 10-5. 사용자 직접 포지션 정리 대처 `[설계 결정]`

Stage 2~3에서 Python이 주기적으로(30초마다) OKX 포지션을 확인한다. 포지션이 예상과 다르게 사라진 경우:

1. 텔레그램: "포지션이 사라졌습니다 — 직접 정리하셨나요?"
2. 5분 대기
3. 무응답 시: OKX `GET /api/v5/trade/orders-history-archive` 조회 → CHLOE에게 전달 → CHLOE가 상황 판단 후 거래 기록 업데이트 및 복기 수행

---

## 11. 시스템 프롬프트 전문

### Part A — 정체성 + 절대 규칙 (cache_control 적용)

```
너는 CHLOE(클로이). 자율적으로 학습하고 성장하는 BTC 트레이더 AI.

[궁극적 목표]
스스로 분석, 매매, 복기, 학습하여 독립적 트레이더로 성장.
너의 모든 판단과 기록은 자율적이다. 형식, 내용, 분류를 스스로 결정한다.

[핵심 철학]
- 시간 의존 분석을 최소화한다. 다루는 건 '언제'가 아니라 '왜'.
  가격이 움직이는 이유, 구조가 바뀌는 이유에 집중한다.
- 인사이트는 느낌이 아닌 통계에서 도출한다.
  Python이 제공하는 통계 결과를 먼저 확인하고, 그 데이터에 기반해 인사이트를 작성한다.
- 기록의 형식과 내용은 CHLOE가 자율적으로 결정한다.

[절대 규칙 — 변경 불가]
1. SL 없이 진입 금지
2. 1회 손실 ≤ 자본 5%
3. 동시 포지션 최대 1개
4. 레버리지 ≤ 10×
5. SL을 불리한 방향으로 이동 금지
6. 시장가 진입 시 현재가 대비 0.5% 이상 슬리피지 괴리 시 진입 차단
(위 규칙은 Python이 강제하며, 너의 주문이 규칙에 위반되면 자동 차단된다)

[재분석 판단]
이전 분석 결과를 함께 받는다. 먼저 "여전히 유효한가"를 판단한다.
유효하면 "변동 없음"으로 끝내고 새 분석을 하지 않는다.
불필요한 재분석은 비용을 소모한다.

[사용자 초기 힌트]
"횡보는 단순 수평이 아니라 내부 구조가 있다 — RSI 흐름, 거래량 변화,
횡보의 기울기를 관찰. 돌파 후 횡보가 이어지면 진짜 돌파, 즉시 되돌림은
페이크아웃 가능성이 높다."
→ 이것은 힌트일 뿐. 직접 검증하고 발전시킬 것.
```

### Part B — 분석·복기 프레임워크 (cache_control 적용)

```
[분석 절차]
1. 큰 그림(1D) → 중기(4H) → 단기(1H/15m) 순서로 분석
2. 시장 환경 태그 확인 (추세/변동성/펀딩/요일)
3. 시나리오 도출: 가격이 이 수준에 도달하면 어떤 구조적 이유로 어떤 방향을 기대하는지
4. 알림 가격 설정 + 유효 조건 기록

[진입 판단]
- 알림 도달 시 1H + 15m 재검증
- 시나리오 작성 시점과 현재 상태가 다르면 미진입 가능
- 진입 시 반드시 SL 포함. TP는 선택.
- acceptable_price_range를 지정하면 가격 범위 밖에서 진입이 차단됨

[포지션 관리]
- TP1 도달 시 50% 청산, SL을 진입가로 이동
- TP2 도달 시 나머지 청산
- 트레일링 스톱 전략도 가능 (TP 없이 SL만 이동)

[복기 절차]
1. 거래 메모 작성 (자유형 — save_trade_memo 도구)
2. Python이 제공하는 통계 결과 확인
3. 통계 기반 인사이트 도출 (save_insight 도구)
4. 필요 시 원칙 승격 (save_principle 도구)
5. 복기가 끝나면 conclude_review 도구로 조기 종료 가능

[과최적화 방지]
- 샘플 수 5회 미만 인사이트에 높은 가중치 부여 금지
- 실패율 40% 초과 인사이트는 신뢰도 하향 (Python 자동)
- 14일 미사용 인사이트는 cold store 이동 (Python 자동)
- 통계 없이 "느낌"으로 인사이트를 만들지 않는다
```

### Part C — 동적 상태 (매 호출 시 갱신, 캐싱 없음)

```
[현재 상태]
현재가: {price} USDT
24H 변동: {change_24h}%
시장 환경: 추세={trend}, 변동성={volatility}, 펀딩={funding}, 요일={day_type}

[포지션]
{position_summary 또는 "없음"}

[잔고]
{balance} USDT (Stage {stage})

[활성 시나리오]
{scenarios 또는 "없음"}

[활성 알림]
{alerts 또는 "없음"}

[활성 원칙]
{principles 또는 "아직 없음"}

[활성 인사이트]
{insights 또는 "아직 없음"}

[최근 10 거래 요약]
{recent_trades 또는 "거래 이력 없음"}

[성과 통계]
오늘 손익: {daily_pnl} USDT
주간 손익: {weekly_pnl} USDT
전체: {total_trades}회, 승률 {win_rate}%, 평균 R:R {avg_rr}, EV {ev}
환경별: {by_environment_summary}

[이전 분석 결론]
{last_analysis_conclusion 또는 "첫 분석"}

[트리거]
{trigger_type}: {trigger_description}
```

---

## 12. Claude 도구 정의 (tools.py)

CHLOE가 사용할 수 있는 도구 목록. 각 도구는 JSON Schema로 정의된다.

### 12-1. 분석·시나리오 도구

**`get_candles`** — 특정 타임프레임의 캔들 데이터 요청

```json
Copy{
  "name": "get_candles",
  "description": "특정 타임프레임의 캔들 데이터를 요청한다.",
  "input_schema": {
    "type": "object",
    "properties": {
      "timeframe": {"type": "string", "enum": ["1D", "4H", "1H", "15m"]},
      "count": {"type": "integer", "minimum": 1, "maximum": 200}
    },
    "required": ["timeframe", "count"]
  }
}
```

**`save_analysis`** — 시장 분석 결과 저장

```json
Copy{
  "name": "save_analysis",
  "description": "시장 분석 결과를 저장한다.",
  "input_schema": {
    "type": "object",
    "properties": {
      "direction": {"type": "string", "enum": ["bullish", "bearish", "neutral"]},
      "key_levels": {"type": "object", "properties": {
        "support": {"type": "array", "items": {"type": "number"}},
        "resistance": {"type": "array", "items": {"type": "number"}}
      }},
      "summary": {"type": "string"},
      "confidence": {"type": "string", "enum": ["low", "medium", "high"]}
    },
    "required": ["direction", "summary"]
  }
}
```

**`save_scenario`** — 시나리오 저장

```json
Copy{
  "name": "save_scenario",
  "description": "트레이딩 시나리오를 저장한다.",
  "input_schema": {
    "type": "object",
    "properties": {
      "scenario_id": {"type": "string"},
      "description": {"type": "string"},
      "direction": {"type": "string", "enum": ["long", "short"]},
      "entry_zone": {"type": "object", "properties": {
        "min": {"type": "number"}, "max": {"type": "number"}
      }},
      "sl_price": {"type": "number"},
      "tp_prices": {"type": "array", "items": {"type": "number"}},
      "valid_condition": {"type": "string"}
    },
    "required": ["scenario_id", "description", "direction"]
  }
}
```

**`set_alert`** — 가격 알림 설정

```json
Copy{
  "name": "set_alert",
  "description": "가격 알림을 설정한다.",
  "input_schema": {
    "type": "object",
    "properties": {
      "price": {"type": "number"},
      "direction": {"type": "string", "enum": ["above", "below"]},
      "scenario_id": {"type": "string"},
      "valid_condition": {"type": "string"}
    },
    "required": ["price", "direction"]
  }
}
```

**`remove_alert`** — 가격 알림 제거

```json
Copy{
  "name": "remove_alert",
  "description": "기존 가격 알림을 제거한다.",
  "input_schema": {
    "type": "object",
    "properties": {
      "alert_id": {"type": "string"}
    },
    "required": ["alert_id"]
  }
}
```

### 12-2. 매매 도구

**`open_position`** — 포지션 진입

```json
Copy{
  "name": "open_position",
  "description": "새 포지션을 연다. SL 필수. TP 선택.",
  "input_schema": {
    "type": "object",
    "properties": {
      "action": {"type": "string", "enum": ["open_long", "open_short"]},
      "ordType": {"type": "string", "enum": ["market", "limit"], "default": "market"},
      "px": {"type": "number", "description": "지정가 주문 시 가격. ordType=limit일 때 필수."},
      "sl_price": {"type": "number"},
      "tp_price": {"type": "number"},
      "acceptable_price_range": {
        "type": "object",
        "properties": {
          "min": {"type": "number"},
          "max": {"type": "number"}
        }
      },
      "reason": {"type": "string"}
    },
    "required": ["action", "sl_price", "reason"]
  }
}
```

**`close_position`** — 포지션 청산

```json
Copy{
  "name": "close_position",
  "description": "현재 포지션을 청산한다.",
  "input_schema": {
    "type": "object",
    "properties": {
      "close_percent": {"type": "number", "minimum": 0, "maximum": 100, "default": 100},
      "reason": {"type": "string"}
    },
    "required": ["reason"]
  }
}
```

**`modify_sl`** — SL 가격 수정

```json
Copy{
  "name": "modify_sl",
  "description": "기존 SL 가격을 수정한다. 유리한 방향으로만 가능.",
  "input_schema": {
    "type": "object",
    "properties": {
      "new_sl_price": {"type": "number"},
      "reason": {"type": "string"}
    },
    "required": ["new_sl_price", "reason"]
  }
}
```

**`modify_tp`** — TP 가격 수정/추가/제거

```json
Copy{
  "name": "modify_tp",
  "description": "TP 가격을 수정, 추가, 또는 제거한다.",
  "input_schema": {
    "type": "object",
    "properties": {
      "new_tp_price": {"type": "number", "description": "0이면 TP 제거"},
      "reason": {"type": "string"}
    },
    "required": ["reason"]
  }
}
```

### 12-3. 학습 도구

**`save_trade_memo`** — 거래 메모 저장

```json
Copy{
  "name": "save_trade_memo",
  "description": "거래에 대한 자유형 메모를 저장한다.",
  "input_schema": {
    "type": "object",
    "properties": {
      "trade_id": {"type": "string"},
      "memo": {"type": "string"}
    },
    "required": ["trade_id", "memo"]
  }
}
```

**`save_insight`** — 인사이트 저장

```json
Copy{
  "name": "save_insight",
  "description": "통계 기반 인사이트를 저장한다.",
  "input_schema": {
    "type": "object",
    "properties": {
      "content": {"type": "string"},
      "category": {"type": "string", "enum": ["entry", "exit", "risk", "market", "general"]},
      "supporting_data": {"type": "string", "description": "이 인사이트를 뒷받침하는 통계 요약"}
    },
    "required": ["content", "category", "supporting_data"]
  }
}
```

**`save_principle`** — 원칙 저장

```json
Copy{
  "name": "save_principle",
  "description": "검증된 인사이트를 트레이딩 원칙으로 승격한다.",
  "input_schema": {
    "type": "object",
    "properties": {
      "content": {"type": "string"},
      "based_on_insight_id": {"type": "string"}
    },
    "required": ["content"]
  }
}
```

**`update_analysis_routine`** — 지표 설정 변경

```json
Copy{
  "name": "update_analysis_routine",
  "description": "지표의 활성 여부와 파라미터를 변경한다.",
  "input_schema": {
    "type": "object",
    "properties": {
      "indicator": {"type": "string"},
      "active": {"type": "boolean"},
      "params": {"type": "object"}
    },
    "required": ["indicator", "active"]
  }
}
```

**`conclude_review`** — 복기 조기 종료

```json
Copy{
  "name": "conclude_review",
  "description": "복기를 완료하고 남은 iteration을 건너뛴다.",
  "input_schema": {
    "type": "object",
    "properties": {
      "summary": {"type": "string"}
    },
    "required": ["summary"]
  }
}
```

**`send_telegram`** — 텔레그램 메시지 전송

```json
Copy{
  "name": "send_telegram",
  "description": "사용자에게 텔레그램 메시지를 보낸다.",
  "input_schema": {
    "type": "object",
    "properties": {
      "message": {"type": "string"}
    },
    "required": ["message"]
  }
}
```

---

## 13. 파일 구조 및 모듈 책임

```
trading_bot/
├── config.py              # 모든 설정값 (아래 13-1 참조)
├── config_secret.py       # API 키 (.gitignore)
├── data.py                # OKX REST API 래퍼
├── indicators.py          # 지표 계산 + 시장 환경 태깅
├── tools.py               # Claude 도구 정의 (JSON schema)
├── executor.py            # 규칙 검증 + 주문 실행
├── ai_brain.py            # Claude API 호출 + 프롬프트 조립
├── logger.py              # JSON 캐시 + Sheets 동기화
├── paper_trading.py       # Stage 1 가상 거래 엔진
├── telegram_handler.py    # 텔레그램 봇 폴링 + 명령어
├── websocket_client.py    # WebSocket 연결 + 재연결 + 동기화
├── scheduler.py           # 이벤트 루프 + 트리거 관리
├── main.py                # 진입점 + 시스템 체크
├── cache/                 # JSON 캐시 디렉터리
└── logs/                  # 일별 로그 파일
```

### 13-1. config.py 전체

```python
Copy# ============================================================
# CHLOE AI Trading System — config.py
# ============================================================

# === 거래 설정 ===
SYMBOL = "BTC-USDT-SWAP"
LEVERAGE = 10                         # [사용자 요청] 최대 레버리지
MAX_LOSS_PERCENT = 0.05               # [사용자 요청] 1회 최대 손실 5%
MAX_CONCURRENT_POSITIONS = 1          # [사용자 요청] 절대 1
MAX_ENTRY_SLIPPAGE = 0.005            # [AI 합의] 시장가 진입 슬리피지 제한 0.5%
TRADING_STAGE = 1                     # 1=paper, 2=demo, 3=live

# === Stage 3 서킷 브레이커 ===
STAGE3_DAILY_LOSS_HARD_LIMIT = 0.15   # [AI 합의] Stage 3에서만 활성 — 일일 15% 하드락

# === 타임프레임 ===
TIMEFRAMES = ["1D", "4H", "1H", "15m"]
CANDLE_COUNTS = {"1D": 200, "4H": 200, "1H": 200, "15m": 50}
UPDATE_COUNTS = {"1D": 20, "4H": 30, "1H": 30, "15m": 50}

# === AI 설정 ===
CLAUDE_MODEL = "claude-sonnet-4-5-20250929"  # 구현 시 최신 모델 확인
MAX_CLAUDE_CALLS_PER_HOUR = 10
EXEMPT_TRIGGERS = ["alert_triggered", "trade_closed", "immediate_review"]
MAX_ITERATIONS = {
    "first_analysis": 5,
    "alert_triggered": 4,
    "trade_closed": 5,
    "periodic_review": 3,
    "user_message": 3,
    "immediate_review": 3,
    "meta_check": 2
}

# === 시뮬레이션 (Stage 1) ===
SIM_TAKER_FEE = 0.0005               # 0.05%
SIM_MIN_SLIPPAGE = 0.0003            # 0.03%

# === 동기화 및 타이머 ===
SHEETS_SYNC_INTERVAL = 300            # 5분
TELEGRAM_POLL_INTERVAL = 5            # 5초
HEARTBEAT_INTERVAL = 3600             # 1시간
ALERT_THROTTLE_SECONDS = 60           # 동일 에러 1분 묶기
COLD_STORE_DAYS = 14                  # 인사이트 cold store 기한
STATS_REBUILD_INTERVAL = 86400        # 24시간
POSITION_CHECK_INTERVAL = 30          # 포지션 확인 주기 (Stage 2~3)

# === Stage 전환 조건 ===
MIN_TRADES_FOR_TRANSITION = 50
MIN_DAYS_FOR_TRANSITION = 60
MAX_MDD_FOR_TRANSITION = 0.20        # 20%
Copy
```

### 13-2. config_secret.py (gitignore 대상)

```python
Copy# OKX API
OKX_API_KEY = ""
OKX_SECRET_KEY = ""
OKX_PASSPHRASE = ""

# Claude API
CLAUDE_API_KEY = ""

# Telegram
TELEGRAM_BOT_TOKEN = ""
TELEGRAM_CHAT_ID = ""

# Google Sheets
GOOGLE_SHEETS_CREDS_FILE = "credentials.json"
GOOGLE_SHEETS_SPREADSHEET_ID = ""
```

### 13-3. 모듈별 상세 책임

**data.py**:

- OKX REST API 래퍼. 모든 요청에 timeout(10초) 적용.
- HTTP 429 응답 시: `Retry-After` 헤더 확인 → `time.sleep` → 재시도 (최대 3회, 지수 백오프: 2s, 4s, 8s).
- HTTP 5xx 응답 시: 동일 재시도 로직.
- 3회 실패 시: `RetryLimitError` 예외 발생.
- 함수: `get_candles()`, `get_ticker()`, `get_balance()`, `get_positions()`, `get_funding_rate()`, `get_instruments()`, `place_order()`, `cancel_order()`, `amend_algos()`, `get_algo_orders_pending()`

**indicators.py**:

- 기술 지표 계산: RSI, EMA, MACD, Bollinger, OBV, VWAP, ATR, ADX, 스윙 고점/저점, 피보나치
- 시장 환경 태깅: `get_market_environment()` → `{"trend": "up", "volatility": "normal", "funding": "neutral", "day": "weekday"}`
- `trend_strength` 계산
- `analysis_routine.json`에서 활성 지표를 읽어 필요한 것만 계산

**executor.py**:

- 규칙 검증 엔진. 진입 전 체크리스트:
    1. SL 존재 확인
    2. 포지션 크기 계산 → 손실 ≤ 5% 검증
    3. 동시 포지션 수 ≤ 1 확인
    4. 레버리지 ≤ 10× 확인
    5. 슬리피지 검증 (acceptable_price_range + MAX_ENTRY_SLIPPAGE)
    6. Stage 3: 일일 누적 손실 15% 확인
- `TRADING_STAGE`에 따라 분기: 1→paper_trading, 2→OKX demo, 3→OKX live
- 포지션 크기 조정 시 사유 로깅
- 중복 주문 방지: 고유 `clOrdId` 생성 (`chloe_{YYYYMMDD}_{sequence}`)
- SL 등록 실패 시 즉시 청산 로직 (5초 타임아웃)
- SL 방향 검증 (불리한 방향 차단)

**ai_brain.py**:

- Claude API 호출 관리
- 3-part 프롬프트 조립 (`cache_control` 적용)
- 도구 호출 처리 (tool_use → tool_result 루프)
- max_iterations 카운트 및 강제 종료
- 호출 횟수/비용 추적 (시간당 제한 검사)
- 실패 시 재시도 (최대 3회, 30초 간격)
- 비동기 호출: Claude 호출을 별도 스레드에서 실행, 메인 루프 차단하지 않음 `[AI 합의 — Grok 제안]`

**paper_trading.py**:

- Stage 1 가상 거래 엔진
- 가상 진입/청산: 알림 도달 시점 가격 ± 슬리피지
- 수수료 시뮬레이션: taker 0.05%
- 펀딩비 시뮬레이션: 8시간마다 실제 OKX 펀딩비율 조회 적용
- `paper_position.json` 관리

**logger.py**:

- JSON 캐시 읽기/쓰기 (`.bak` 백업 포함)
- Google Sheets 동기화 (gspread)
- 일별 로그 파일 (`logs/YYYY-MM-DD.log`)
- 호출 비용 추적 로그

**telegram_handler.py**:

- 봇 폴링 (5초 간격)
- 빠른 명령어 처리 (Python 직접)
- 일반 메시지 → CHLOE 전달
- 알림 throttling (동일 에러 1분 내 묶기)
- 크래시 시 마지막 로그 파일 txt 전송 시도
- /pause, /panic 시 `system_state.json` 업데이트

**websocket_client.py**:

- OKX WebSocket 연결: `tickers` (가격), `orders` (주문 상태), `positions` (포지션)
- 재연결 로직: 끊김 감지 → 즉시 재연결 시도 (최대 5회, 간격 2s, 4s, 8s, 16s, 32s)
- 재연결 성공 시: REST API로 현재 포지션 조회 → 내부 상태와 비교 → 불일치 시 REST 데이터를 진실로 삼아 강제 업데이트 + 텔레그램 알림 + 로그 `[AI 합의]`
- 모든 재연결 실패 시: REST 폴링 폴백(30초 간격) + 텔레그램 알림

**scheduler.py**:

- 이벤트 루프 관리
- 가격 알림 체크 (WebSocket 가격 vs alerts.json)
- 비긴급 트리거 배칭 (마지막 Claude 호출 후 2~3분 이내 실행)
- 통계 재계산 트리거 (24시간 경과 시)
- Sheets 동기화 타이머 (5분)
- 하트비트 타이머 (1시간)
- Cold store 정리 (24시간)
- 포지션 확인 (30초, Stage 2~3)
- /pause 상태 관리 (paused=true면 새 진입 트리거 차단)

**main.py**:

- 캐시 무결성 확인 + 초기화
- 시스템 상태 확인 (정상/비정상 종료 판별)
- API 연결 테스트 (OKX, Claude, Sheets)
- 계약 사양 조회 (ctVal, lotSz)
- 레버리지 설정
- 비정상 종료 시: OKX 이력 조회 → CHLOE에게 보고
- 최상위 try-except: 크래시 시 텔레그램 에러 전송 시도 + 로그 파일 전송
- 정상 종료 시: `system_state.json`에 `{"shutdown": "normal", "time": "..."}` 기록

---

## 14. 종료 및 재시작

### 14-1. 정상 종료 (`/stop`)

1. 열린 포지션 확인 → 있으면 텔레그램 경고: "SL/TP가 OKX에 등록돼 있으나 CHLOE가 관리 불가. 트레일링 스톱/부분 청산 불가."
2. `system_state.json`에 `{"shutdown": "normal", "time": "...", "paused": false}` 기록
3. WebSocket 정상 종료, 스레드 정리, 프로그램 종료

### 14-2. 비정상 종료 (터미널 닫기, 컴퓨터 끄기, 크래시)

`system_state.json`에 "normal" 기록이 없으면 비정상 종료로 판단.

프로그램 크래시 시: 최상위 try-except에서 텔레그램으로 에러 메시지 + 마지막 로그 파일 전송 시도.

### 14-3. 재시작 시

1. `system_state.json` 확인 → 비정상이면 텔레그램 알림: "비정상 종료 감지. OKX 이력 확인 중..."
2. 캐시 파일 무결성 확인 (손상 시 `.bak`에서 복구)
3. OKX에서 마지막 종료 이후 체결 내역 조회 → CHLOE에게 보고
4. 첫 가격 피드 수신 후 첫 분석 트리거

### 14-4. 포지션 보호

SL/TP는 OKX 서버에 algo order로 등록되어 있으므로, 프로그램이 꺼져도 SL/TP는 실행된다. 단, 트레일링 스톱 이동, TP1 부분 청산 등은 프로그램 실행 중이어야 한다.

---

## 15. 오류 처리 전체 요약

|상황|처리|텔레그램 알림|
|---|---|---|
|OKX REST 429|Retry-After 대기 + 재시도 (최대 3회, 지수 백오프)|3회 실패 시|
|OKX REST 5xx|동일 재시도|3회 실패 시|
|OKX REST 429 연속 + SL 미등록 확인 불가|60초 대기 후 재시도 → 실패 시 포지션 강제 청산|즉시|
|OKX WebSocket 끊김|즉시 재연결 (최대 5회) → 실패 시 REST 폴백|재연결 실패 시|
|WebSocket 재연결 후 포지션 불일치|REST 데이터를 진실로 삼아 강제 동기화|즉시|
|Claude API 실패|30초 대기 후 재시도 (최대 3회) → 트리거 건너뛰기|3회 실패 시|
|Google Sheets 실패|로컬 JSON 유지. 연결 복구 시 자동 재시도.|첫 실패 시|
|캐시 파일 손상|`.bak`에서 복구. 둘 다 없으면 초기값 생성.|즉시|
|SL 등록 실패 (진입 후)|즉시 전량 시장가 청산|즉시|
|포지션 예상 외 소실 (Stage 2~3)|텔레그램 질문 → 5분 대기 → 자동 OKX 이력 조회|즉시|
|프로그램 크래시|텔레그램 에러 + 로그 파일 전송 시도|시도|

---

## 16. 외부 하트비트 감시 `[AI 합의]`

프로그램 크래시 시 텔레그램 알림 자체가 불가능할 수 있다. UptimeRobot(무료)으로 HTTP 핑 체크 설정:

1. 프로그램이 1시간마다 UptimeRobot의 HTTP(s) 모니터 URL을 호출 (GET 요청)
2. 호출이 없으면 UptimeRobot이 이메일/SMS로 알림
3. 사용자가 확인 후 프로그램 재시작

---

## 17. 개발 순서

| 단계  | 파일                               | 의존성                                | 설명                    |
| --- | -------------------------------- | ---------------------------------- | --------------------- |
| 1   | `config.py` + `config_secret.py` | 없음                                 | 모든 설정값 + API 키        |
| 2   | `data.py`                        | config                             | OKX REST 래퍼 + 429 핸들링 |
| 3   | `indicators.py`                  | data                               | 지표 계산 + 시장 태깅         |
| 4   | `logger.py`                      | config                             | JSON 캐시 + Sheets 동기화  |
| 5   | `paper_trading.py`               | data, indicators, logger           | Stage 1 가상 거래 엔진      |
| 6   | `executor.py`                    | data, paper_trading, logger        | 규칙 검증 + 주문 실행         |
| 7   | `tools.py`                       | 없음                                 | Claude 도구 정의 (스키마만)   |
| 8   | `ai_brain.py`                    | config, tools, logger              | Claude API + 프롬프트     |
| 9   | `telegram_handler.py`            | config, logger, executor, ai_brain | 텔레그램 봇                |
| 10  | `websocket_client.py`            | config, data                       | WebSocket 클라이언트       |
| 11  | `scheduler.py`                   | 모든 모듈                              | 이벤트 루프                |
| 12  | `main.py`                        | 모든 모듈                              | 진입점 + 통합              |
| 13  | 통합 테스트                           | 모든 모듈                              | 연결 테스트 + 시나리오 테스트     |

---

## 18. 현재 상태

| 항목           | 값                 | 비고              |
| ------------ | ----------------- | --------------- |
| 잔고           | ~$34.93 USDT      |                 |
| Stage        | 1 (Paper Trading) |                 |
| 포지션 모드       | 양방향(Hedge)        | ✅ 설정 완료         |
| 마진 모드        | 교차(Cross)         | ✅ 설정 완료         |
| OKX 데모 API 키 | 미준비               | Stage 2 전환 시 필요 |
| 상품           | BTC-USDT-SWAP     | ✅ 확인 완료         |

---

## 19. 리뷰어 참고사항

이 기획안의 핵심은 **CHLOE의 자율성**이다. 하드코딩된 규칙은 6개(SL 필수, 5% 손실 한도, 포지션 1개, 레버리지 10×, SL 후퇴 금지, 슬리피지 0.5% 제한)로 최소화되어 있으며, 나머지 모든 판단(진입 시점, 청산 방식, 지표 선택, 학습 내용, 기록 방식)은 CHLOE가 자율적으로 결정한다.

리뷰 시 중점 확인 사항:

1. CHLOE의 자율성을 불필요하게 제한하는 항목이 있는가?
2. 비용 효율을 더 개선할 수 있는 구조적 변경이 있는가?
3. 하드코딩된 6개 절대 규칙에 빠진 안전장치가 있는가?
4. 통계 선행 → 인사이트 도출 흐름에 개선 가능한 부분이 있는가?
5. 실행 지연(Claude 호출 3~10초)이 매매에 미치는 영향과 대처가 충분한가?

---

_이 문서는 CHLOE AI Trading System의 설계·구현·코드 리뷰·수정 요청에 대한 유일한 기준 문서이다. 이 문서에 명시되지 않은 사항은 구현자가 판단하되, 섹션 0-2의 설계 원칙을 따른다._