from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

from config import SYMBOL
from data import get_funding_rate


ROUTINE_PATH = Path("cache/analysis_routine.json")


DEFAULT_ANALYSIS_ROUTINE = {
    "indicators": [
        {"name": "RSI", "active": True, "params": {"period": 14}},
        {"name": "MACD", "active": False, "params": {"fast": 12, "slow": 26, "signal": 9}},
        {"name": "BB", "active": False, "params": {"period": 20, "std": 2}},
        {"name": "OBV", "active": False, "params": {}},
        {"name": "VWAP", "active": False, "params": {}},
        {"name": "ADX", "active": True, "params": {"period": 14}},
        {"name": "ATR", "active": True, "params": {"period": 14}},
    ],
    "version": "1.0",
    "last_updated_by": "CHLOE",
    "last_updated_at": "",
}


def _load_analysis_routine() -> Dict[str, Any]:
    if not ROUTINE_PATH.exists():
        ROUTINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        ROUTINE_PATH.write_text(json.dumps(DEFAULT_ANALYSIS_ROUTINE, ensure_ascii=False, indent=2))
        return DEFAULT_ANALYSIS_ROUTINE

    try:
        loaded = json.loads(ROUTINE_PATH.read_text())
    except json.JSONDecodeError:
        ROUTINE_PATH.write_text(json.dumps(DEFAULT_ANALYSIS_ROUTINE, ensure_ascii=False, indent=2))
        return DEFAULT_ANALYSIS_ROUTINE

    if "indicators" not in loaded:
        loaded = DEFAULT_ANALYSIS_ROUTINE
    return loaded


def _is_active(routine: Dict[str, Any], name: str) -> bool:
    for indicator in routine.get("indicators", []):
        if indicator.get("name") == name:
            return bool(indicator.get("active", False))
    return False


def _params(routine: Dict[str, Any], name: str, defaults: Dict[str, Any]) -> Dict[str, Any]:
    for indicator in routine.get("indicators", []):
        if indicator.get("name") == name:
            p = indicator.get("params", {})
            merged = defaults.copy()
            merged.update(p)
            return merged
    return defaults


def _wilder_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift(1)).abs()
    low_close = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def _adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    up_move = df["high"].diff()
    down_move = -df["low"].diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr = _atr(df, period=1)
    atr_smooth = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / atr_smooth.replace(0, np.nan)
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / atr_smooth.replace(0, np.nan)
    dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)).fillna(0)
    return dx.ewm(alpha=1 / period, adjust=False).mean()


def _macd(close: pd.Series, fast: int, slow: int, signal: int) -> pd.DataFrame:
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return pd.DataFrame({"macd": macd_line, "signal": signal_line, "hist": hist})


def _bollinger(close: pd.Series, period: int, std: float) -> pd.DataFrame:
    mid = close.rolling(period).mean()
    dev = close.rolling(period).std(ddof=0)
    upper = mid + std * dev
    lower = mid - std * dev
    return pd.DataFrame({"bb_mid": mid, "bb_upper": upper, "bb_lower": lower})


def _obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff().fillna(0))
    return (direction * volume).cumsum()


def _vwap(df: pd.DataFrame) -> pd.Series:
    typical = (df["high"] + df["low"] + df["close"]) / 3
    cum_vol = df["volume"].cumsum().replace(0, np.nan)
    return (typical * df["volume"]).cumsum() / cum_vol


def _swing_points(df: pd.DataFrame, lookback: int = 3) -> Dict[str, float | None]:
    highs = df["high"]
    lows = df["low"]
    swing_highs = highs[(highs.shift(lookback) < highs) & (highs.shift(-lookback) < highs)]
    swing_lows = lows[(lows.shift(lookback) > lows) & (lows.shift(-lookback) > lows)]
    return {
        "swing_high": float(swing_highs.iloc[-1]) if not swing_highs.empty else None,
        "swing_low": float(swing_lows.iloc[-1]) if not swing_lows.empty else None,
    }


def _fibonacci_levels(swing_high: float | None, swing_low: float | None) -> Dict[str, float] | Dict[str, None]:
    if swing_high is None or swing_low is None:
        return {"0.236": None, "0.382": None, "0.5": None, "0.618": None, "0.786": None}

    diff = swing_high - swing_low
    return {
        "0.236": swing_high - diff * 0.236,
        "0.382": swing_high - diff * 0.382,
        "0.5": swing_high - diff * 0.5,
        "0.618": swing_high - diff * 0.618,
        "0.786": swing_high - diff * 0.786,
    }


def _volume_cluster_ratio(volume: pd.Series, window: int = 20) -> pd.Series:
    vol_mean = volume.rolling(window).mean().replace(0, np.nan)
    return (volume / vol_mean).fillna(1.0)


def generate_indicator_labels(candles_1h: list, analysis_routine: dict = None) -> list:
    """
    진입 시점의 지표 값 기반 라벨을 자동 생성한다.
    기획안 섹션 4-5: by_indicator 통계의 키로 사용.
    반환 예: ["RSI_below_30", "EMA_cross_bullish", "ADX_above_25"]
    """
    labels = []
    if not candles_1h:
        return labels

    closes = [float(c[4]) if isinstance(c, list) else float(c.get("c", c.get("close", 0))) for c in candles_1h]
    if len(closes) < 50:
        return labels

    # RSI
    try:
        rsi_val = float(_wilder_rsi(pd.Series(closes), 14).iloc[-1])
        if rsi_val < 30:
            labels.append("RSI_below_30")
        elif rsi_val > 70:
            labels.append("RSI_above_70")
        elif 30 <= rsi_val <= 40:
            labels.append("RSI_30_40")
        elif 60 <= rsi_val <= 70:
            labels.append("RSI_60_70")
    except Exception:
        pass

    # EMA cross
    try:
        close_s = pd.Series(closes)
        ema20 = float(close_s.ewm(span=20, adjust=False).mean().iloc[-1])
        ema50 = float(close_s.ewm(span=50, adjust=False).mean().iloc[-1])
        if ema20 > ema50:
            labels.append("EMA_cross_bullish")
        else:
            labels.append("EMA_cross_bearish")
    except Exception:
        pass

    # ADX
    if analysis_routine:
        adx_active = any(
            ind.get("name") == "ADX" and ind.get("active")
            for ind in analysis_routine.get("indicators", [])
        )
    else:
        adx_active = True

    if adx_active:
        try:
            highs = [float(c[2]) if isinstance(c, list) else float(c.get("h", c.get("high", 0))) for c in candles_1h]
            lows = [float(c[3]) if isinstance(c, list) else float(c.get("l", c.get("low", 0))) for c in candles_1h]
            adx_df = pd.DataFrame({"high": highs, "low": lows, "close": closes})
            adx_val = float(_adx(adx_df, 14).iloc[-1])
            if adx_val > 25:
                labels.append("ADX_above_25")
            else:
                labels.append("ADX_below_25")
        except Exception:
            pass

    # ATR volatility
    try:
        highs = [float(c[2]) if isinstance(c, list) else float(c.get("h", c.get("high", 0))) for c in candles_1h]
        lows = [float(c[3]) if isinstance(c, list) else float(c.get("l", c.get("low", 0))) for c in candles_1h]
        atr_df = pd.DataFrame({"high": highs, "low": lows, "close": closes})
        atr_val = float(_atr(atr_df, 14).iloc[-1])
        if closes[-1] > 0:
            atr_pct = atr_val / closes[-1]
            if atr_pct > 0.02:
                labels.append("ATR_high_vol")
    except Exception:
        pass

    return labels


def calculate_indicators(candles: List[Dict[str, Any]]) -> Dict[str, Any]:
    df = pd.DataFrame(candles).copy()
    if df.empty:
        return {}

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    routine = _load_analysis_routine()

    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["rsi14"] = _wilder_rsi(df["close"], period=14)
    df["volume_cluster_ratio"] = _volume_cluster_ratio(df["volume"], window=20)

    swings = _swing_points(df)
    fib = _fibonacci_levels(swings["swing_high"], swings["swing_low"])

    # ADX와 ATR은 시장 환경 태깅에 필수이므로 항상 계산
    # analysis_routine의 active 여부는 CHLOE에게 결과를 전달할지 여부만 결정
    atr_params = _params(routine, "ATR", {"period": 14})
    df["atr"] = _atr(df, period=int(atr_params["period"]))

    adx_params = _params(routine, "ADX", {"period": 14})
    df["adx"] = _adx(df, period=int(adx_params["period"]))

    if _is_active(routine, "MACD"):
        p = _params(routine, "MACD", {"fast": 12, "slow": 26, "signal": 9})
        macd_df = _macd(df["close"], int(p["fast"]), int(p["slow"]), int(p["signal"]))
        df = pd.concat([df, macd_df], axis=1)

    if _is_active(routine, "BB"):
        p = _params(routine, "BB", {"period": 20, "std": 2})
        bb_df = _bollinger(df["close"], int(p["period"]), float(p["std"]))
        df = pd.concat([df, bb_df], axis=1)

    if _is_active(routine, "OBV"):
        df["obv"] = _obv(df["close"], df["volume"])

    if _is_active(routine, "VWAP"):
        df["vwap"] = _vwap(df)

    latest = df.iloc[-1]
    market_env = get_market_environment(df, inst_id=SYMBOL)

    result = {
        "price_structure": {
            "swing_high": swings["swing_high"],
            "swing_low": swings["swing_low"],
        },
        "rsi": float(latest.get("rsi14", np.nan)),
        "ema20": float(latest.get("ema20", np.nan)),
        "ema50": float(latest.get("ema50", np.nan)),
        "volume_cluster_ratio": float(latest.get("volume_cluster_ratio", np.nan)),
        "fibonacci_retracement": fib,
        "market_environment": market_env,
        "trend_strength": float(((latest.get("ema20", np.nan) - latest.get("ema50", np.nan)) / latest.get("ema50", np.nan)) * 100) if latest.get("ema50", np.nan) else 0.0,
    }

    optional_fields = ["macd", "signal", "hist", "bb_mid", "bb_upper", "bb_lower", "obv", "vwap"]
    for field in optional_fields:
        if field in df.columns:
            val = latest.get(field)
            result[field] = float(val) if pd.notna(val) else None

    # ADX/ATR은 계산은 항상 수행하되, active일 때만 CHLOE 결과에 노출
    if _is_active(routine, "ADX") and "adx" in df.columns:
        val = latest.get("adx")
        result["adx"] = float(val) if pd.notna(val) else None
    if _is_active(routine, "ATR") and "atr" in df.columns:
        val = latest.get("atr")
        result["atr"] = float(val) if pd.notna(val) else None

    return result


def get_market_environment(df: pd.DataFrame, inst_id: str = SYMBOL) -> Dict[str, Any]:
    """시장 환경 태깅. df는 1D 캔들 기준 지표가 계산된 DataFrame이어야 한다.
    변동성 = ATR(14) / 최근 20캔들 ATR 평균 — 1D 기준이므로 20일 평균."""
    if df.empty:
        return {"trend": "sideways", "volatility": "normal", "funding": "neutral", "day": "weekday", "trend_strength": 0.0}

    last = df.iloc[-1]
    ema20 = float(last.get("ema20", np.nan))
    ema50 = float(last.get("ema50", np.nan))
    adx = float(last.get("adx", 0.0)) if pd.notna(last.get("adx", np.nan)) else 0.0
    atr = float(last.get("atr", 0.0)) if pd.notna(last.get("atr", np.nan)) else 0.0

    if adx <= 25:
        trend = "sideways"
    else:
        trend = "up" if ema20 > ema50 else "down"

    atr_avg20 = float(df["atr"].tail(20).mean()) if "atr" in df.columns else 0.0
    ratio = atr / atr_avg20 if atr_avg20 else 1.0
    if ratio > 1.3:
        volatility = "high"
    elif ratio < 0.7:
        volatility = "low"
    else:
        volatility = "normal"

    funding = "neutral"
    try:
        funding_raw = get_funding_rate(inst_id)
        funding_data = funding_raw.get("data", [])
        funding_rate = float(funding_data[0].get("fundingRate", 0.0)) if funding_data else 0.0
        if funding_rate > 0.0003:
            funding = "long_bias"
        elif funding_rate < -0.0003:
            funding = "short_bias"
    except Exception:
        funding = "neutral"

    now_utc = datetime.now(timezone.utc)
    day = "weekend" if now_utc.weekday() >= 5 else "weekday"

    trend_strength = ((ema20 - ema50) / ema50 * 100) if ema50 else 0.0

    return {
        "trend": trend,
        "volatility": volatility,
        "funding": funding,
        "day": day,
        "trend_strength": trend_strength,
    }
