# ============================================================
# CHLOE AI Trading System — config.py
# ============================================================

# === 거래 설정 ===
SYMBOL = "BTC-USDT-SWAP"
LEVERAGE = 10                         # [사용자 요청] 최대 레버리지
MAX_LOSS_PERCENT = 0.05               # [사용자 요청] 1회 최대 손실 5%
MAX_CONCURRENT_POSITIONS = 1          # [사용자 요청] 절대 1
MAX_ENTRY_SLIPPAGE = 0.005            # [AI 합의] 시장가 진입 슬리피지 제한 0.5%
TRADING_STAGE = 1                     # 1=paper, 2=demo, 3=live

# === OKX API URLs ===
REST_BASE = "https://www.okx.com"
WS_PUBLIC = "wss://ws.okx.com:8443/ws/v5/public"
WS_PRIVATE = "wss://ws.okx.com:8443/ws/v5/private"
WS_PUBLIC_DEMO = "wss://wspap.okx.com:8443/ws/v5/public"
WS_PRIVATE_DEMO = "wss://wspap.okx.com:8443/ws/v5/private"

# === Stage 3 서킷 브레이커 ===
STAGE3_DAILY_LOSS_HARD_LIMIT = 0.15   # [AI 합의] Stage 3에서만 활성 — 일일 15% 하드락

# === 타임프레임 ===
TIMEFRAMES = ["1D", "4H", "1H", "15m"]
CANDLE_COUNTS = {"1D": 200, "4H": 200, "1H": 200, "15m": 50}
UPDATE_COUNTS = {"1D": 20, "4H": 30, "1H": 30, "15m": 50}

# === AI 설정 ===
CLAUDE_MODEL = "claude-sonnet-4-5-20250929"  # 구현 시 최신 모델 확인
MAX_CLAUDE_CALLS_PER_HOUR = 15
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

# === 과최적화 방지 ===
OVERFIT_FAILURE_THRESHOLD = 0.40
INSIGHT_CONFIDENCE = {"low": (5, 9), "medium": (10, 19), "high": (20, float("inf"))}

# === Stage 전환 조건 ===
MIN_TRADES_FOR_TRANSITION = 50
MIN_DAYS_FOR_TRANSITION = 60
MAX_MDD_FOR_TRANSITION = 0.20        # 20%

# === 안전 [v4.1] ===
MAX_ENTRY_SLIPPAGE_HIGH_VOL = 0.01
PRICE_STALE_SECONDS = 30
ORDER_COOLDOWN_SECONDS = 60
MAX_CANCEL_RETRIES = 3

# === 학습 품질 [v4.1] ===
MIN_TRADES_FOR_INSIGHT = 3
ENV_BIAS_THRESHOLD = 0.8
TIME_BIAS_DAYS = 7
PRINCIPLE_MIN_SAMPLE = 10
PRINCIPLE_MIN_WINRATE = 0.5
COOLOFF_TRIGGER_COUNT = 3
COOLOFF_LOSS_THRESHOLD = -1.0

# === 손실 점검 [v4.1] ===
CONSECUTIVE_LOSS_ALERT = 3
DAILY_LOSS_ALERT_PERCENT = 0.10

# === 지정가/캐시 [v4.1] ===
LIMIT_ORDER_EXPIRY_MINUTES = 60
INSIGHT_DETAIL_CACHE_TTL = 300

