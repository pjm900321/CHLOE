from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from config import SIM_MIN_SLIPPAGE, SIM_TAKER_FEE, SYMBOL
from data import get_funding_rate
from logger import append_trade_log, read_json_cache, write_json_cache


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _next_trade_id() -> str:
    trades = read_json_cache("trade_log.json")
    return f"trade_{len(trades) + 1:03d}"


def _load_position() -> Dict[str, Any]:
    pos = read_json_cache("paper_position.json")
    if not isinstance(pos, dict):
        pos = {"has_position": False}
    if "has_position" not in pos:
        pos["has_position"] = False
    return pos


def _save_position(pos: Dict[str, Any]) -> None:
    write_json_cache("paper_position.json", pos)


def _dynamic_slippage(price: float, atr: Optional[float]) -> float:
    if price <= 0:
        return SIM_MIN_SLIPPAGE
    atr_value = float(atr) if atr is not None else 0.0
    return max((atr_value / price) * 0.001, SIM_MIN_SLIPPAGE)


def _apply_slippage_price(side: str, raw_price: float, slippage_rate: float, is_entry: bool) -> float:
    if side == "long":
        return raw_price * (1 + slippage_rate) if is_entry else raw_price * (1 - slippage_rate)
    return raw_price * (1 - slippage_rate) if is_entry else raw_price * (1 + slippage_rate)


def open_paper_position(
    side: str,
    entry_price: float,
    size: float,
    sl_price: float,
    tp_price: Optional[float],
    atr: Optional[float] = None,
    market_env: Optional[Dict[str, Any]] = None,
    active_indicators: Optional[list] = None,
) -> Dict[str, Any]:
    pos = _load_position()
    if pos.get("has_position"):
        return {"ok": False, "reason": "paper position already exists", "position": pos}

    slip_rate = _dynamic_slippage(entry_price, atr)
    filled_entry = _apply_slippage_price(side, float(entry_price), slip_rate, is_entry=True)
    notional = filled_entry * float(size)
    entry_fee = notional * SIM_TAKER_FEE

    position = {
        "has_position": True,
        "side": side,
        "entry_price": filled_entry,
        "size": float(size),
        "sl_price": float(sl_price),
        "tp_price": None if tp_price is None else float(tp_price),
        "entry_time": _utc_now_iso(),
        "entry_fee": entry_fee,
        "funding_paid": 0.0,
        "unrealized_pnl": 0.0,
        "slippage": slip_rate,
        "symbol": SYMBOL,
        "env": market_env or {"trend": "unknown", "volatility": "unknown", "funding": "neutral", "day": "weekday"},
        "active_indicators": active_indicators or [],
        "last_funding_time": _utc_now_iso(),
    }
    _save_position(position)
    return {"ok": True, "position": position}


def close_paper_position(
    exit_price: float,
    close_percent: float = 1.0,
    reason: str = "manual",
    atr: Optional[float] = None,
    memo: str = "",
) -> Dict[str, Any]:
    pos = _load_position()
    if not pos.get("has_position"):
        return {"ok": False, "reason": "no paper position"}

    side = pos["side"]
    close_percent = max(0.0, min(1.0, float(close_percent)))
    close_size = float(pos["size"]) * close_percent
    remain_size = float(pos["size"]) - close_size

    slip_rate = _dynamic_slippage(float(exit_price), atr)
    filled_exit = _apply_slippage_price(side, float(exit_price), slip_rate, is_entry=False)

    entry_price = float(pos["entry_price"])
    if side == "long":
        gross_pnl = (filled_exit - entry_price) * close_size
    else:
        gross_pnl = (entry_price - filled_exit) * close_size

    exit_fee = (filled_exit * close_size) * SIM_TAKER_FEE
    funding_paid = float(pos.get("funding_paid", 0.0)) * close_percent
    total_fees = float(pos.get("entry_fee", 0.0)) * close_percent + exit_fee
    net_pnl = gross_pnl - total_fees - funding_paid

    risk_per_unit = abs(entry_price - float(pos["sl_price"]))
    risk_total = risk_per_unit * close_size if risk_per_unit > 0 else 0.0
    pnl_r = (net_pnl / risk_total) if risk_total > 0 else 0.0

    trade_record = {
        "id": _next_trade_id(),
        "side": side,
        "entry_price": entry_price,
        "exit_price": filled_exit,
        "size": close_size,
        "sl_price": float(pos["sl_price"]),
        "tp_price": pos.get("tp_price"),
        "entry_time": pos.get("entry_time", _utc_now_iso()),
        "exit_time": _utc_now_iso(),
        "pnl_usdt": net_pnl,
        "pnl_r": pnl_r,
        "fees": total_fees,
        "funding": funding_paid,
        "slippage": slip_rate,
        "env": pos.get("env", {"trend": "unknown", "volatility": "unknown", "funding": "neutral", "day": "weekday"}),
        "memo": memo,
        "exit_reason": reason,
        "active_indicators": pos.get("active_indicators", []),
    }
    append_trade_log(trade_record)

    if remain_size <= 0:
        _save_position({"has_position": False})
    else:
        pos["size"] = remain_size
        pos["funding_paid"] = float(pos.get("funding_paid", 0.0)) * (1 - close_percent)
        pos["entry_fee"] = float(pos.get("entry_fee", 0.0)) * (1 - close_percent)
        _save_position(pos)

    return {
        "ok": True,
        "closed_size": close_size,
        "remaining_size": max(remain_size, 0.0),
        "trade": trade_record,
    }


def update_paper_sl(new_sl_price: float) -> Dict[str, Any]:
    pos = _load_position()
    if not pos.get("has_position"):
        return {"ok": False, "reason": "no paper position"}
    pos["sl_price"] = float(new_sl_price)
    _save_position(pos)
    return {"ok": True, "position": pos}


def update_paper_tp(new_tp_price: Optional[float]) -> Dict[str, Any]:
    pos = _load_position()
    if not pos.get("has_position"):
        return {"ok": False, "reason": "no paper position"}
    pos["tp_price"] = None if new_tp_price is None else float(new_tp_price)
    _save_position(pos)
    return {"ok": True, "position": pos}


def check_paper_sl_tp(current_price: float, atr: Optional[float] = None) -> Dict[str, Any]:
    pos = _load_position()
    if not pos.get("has_position"):
        return {"ok": True, "triggered": None}

    side = pos["side"]
    sl = float(pos["sl_price"])
    tp = pos.get("tp_price")
    tp = None if tp is None else float(tp)

    if side == "long":
        if current_price <= sl:
            closed = close_paper_position(current_price, close_percent=1.0, reason="sl_hit", atr=atr)
            return {"ok": True, "triggered": "sl", "result": closed}
        if tp is not None and current_price >= tp:
            closed = close_paper_position(current_price, close_percent=1.0, reason="tp_hit", atr=atr)
            return {"ok": True, "triggered": "tp", "result": closed}
    else:
        if current_price >= sl:
            closed = close_paper_position(current_price, close_percent=1.0, reason="sl_hit", atr=atr)
            return {"ok": True, "triggered": "sl", "result": closed}
        if tp is not None and current_price <= tp:
            closed = close_paper_position(current_price, close_percent=1.0, reason="tp_hit", atr=atr)
            return {"ok": True, "triggered": "tp", "result": closed}

    entry = float(pos["entry_price"])
    size = float(pos["size"])
    if side == "long":
        unrealized = (float(current_price) - entry) * size
    else:
        unrealized = (entry - float(current_price)) * size
    pos["unrealized_pnl"] = unrealized
    _save_position(pos)

    return {"ok": True, "triggered": None, "unrealized_pnl": unrealized}


def apply_funding(now: Optional[datetime] = None) -> Dict[str, Any]:
    pos = _load_position()
    if not pos.get("has_position"):
        return {"ok": True, "applied": False, "reason": "no paper position"}

    now_utc = now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)
    last_funding = pos.get("last_funding_time")
    if last_funding:
        last_dt = datetime.fromisoformat(str(last_funding).replace("Z", "+00:00")).astimezone(timezone.utc)
    else:
        last_dt = now_utc

    if now_utc - last_dt < timedelta(hours=8):
        return {"ok": True, "applied": False, "reason": "interval_not_reached"}

    try:
        fr = get_funding_rate(SYMBOL)
        data = fr.get("data", [])
        funding_rate = float(data[0].get("fundingRate", 0.0)) if data else 0.0
    except Exception:
        funding_rate = 0.0

    notional = float(pos["entry_price"]) * float(pos["size"])
    if pos["side"] == "long":
        funding_fee = notional * funding_rate
    else:
        funding_fee = notional * (-funding_rate)

    pos["funding_paid"] = float(pos.get("funding_paid", 0.0)) + funding_fee
    pos["last_funding_time"] = _utc_now_iso()
    _save_position(pos)

    return {"ok": True, "applied": True, "funding_rate": funding_rate, "funding_fee": funding_fee}
