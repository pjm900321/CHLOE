from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from config import (
    LEVERAGE,
    MAX_CONCURRENT_POSITIONS,
    MAX_ENTRY_SLIPPAGE,
    MAX_LOSS_PERCENT,
    STAGE3_DAILY_LOSS_HARD_LIMIT,
    SYMBOL,
    TRADING_STAGE,
)
from data import (
    amend_algos,
    cancel_algos,
    get_algo_orders_pending,
    get_balance,
    get_positions,
    place_order,
)
from logger import read_json_cache
from paper_trading import close_paper_position, open_paper_position


_ORDER_SEQUENCE = 0
CT_VAL: float = 0.01
LOT_SZ: float = 1.0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _next_clordid() -> str:
    global _ORDER_SEQUENCE
    _ORDER_SEQUENCE += 1
    return f"chloe_{_utc_now().strftime('%Y%m%d')}_{_ORDER_SEQUENCE:03d}"


def _extract_balance_usdt(balance_resp: Dict[str, Any]) -> float:
    try:
        details = balance_resp.get("data", [])[0].get("details", [])
        for row in details:
            if row.get("ccy") == "USDT":
                return float(row.get("availEq") or row.get("availBal") or row.get("cashBal") or 0.0)
    except Exception:
        pass
    return 0.0


def _extract_open_positions_count(positions_resp: Dict[str, Any]) -> int:
    count = 0
    for row in positions_resp.get("data", []):
        if abs(float(row.get("pos", 0.0))) > 0:
            count += 1
    return count


def _calc_contracts(balance: float, entry_price: float, sl_price: float, ct_val: float, lot_sz: float) -> Tuple[float, Dict[str, Any]]:
    max_loss = balance * MAX_LOSS_PERCENT
    stop_distance = abs(entry_price - sl_price) * ct_val
    contracts_raw = max_loss / stop_distance if stop_distance > 0 else 0.0
    contracts = math.floor(contracts_raw / lot_sz) * lot_sz if lot_sz > 0 else math.floor(contracts_raw)

    message: Dict[str, Any] = {
        "resized": False,
        "actual_risk": 0.0,
        "notice": "",
    }

    if contracts < 1:
        contracts = 1
        actual_risk = (stop_distance / balance) if balance > 0 else 1.0
        message["actual_risk"] = actual_risk
        if actual_risk > MAX_LOSS_PERCENT:
            message["notice"] = f"최소 1계약이지만 실제 리스크 {actual_risk:.2%}"
            logging.warning("minimum 1 contract risk %.2f%% exceeds max %.2f%%", actual_risk * 100, MAX_LOSS_PERCENT * 100)

    return contracts, message


def _validate_slippage(
    decision_price: float,
    last_price: float,
    acceptable_price_range: Optional[Dict[str, float]],
) -> Tuple[bool, str]:
    if acceptable_price_range:
        min_px = float(acceptable_price_range.get("min", last_price))
        max_px = float(acceptable_price_range.get("max", last_price))
        if not (min_px <= last_price <= max_px):
            return False, f"가격 범위 초과로 진입 취소됨. 현재가 {last_price}"

    if decision_price <= 0:
        return False, "결정 가격이 유효하지 않음"

    drift = abs(last_price - decision_price) / decision_price
    if drift > MAX_ENTRY_SLIPPAGE:
        return False, f"슬리피지 {drift:.2%} > {MAX_ENTRY_SLIPPAGE:.2%}"
    return True, "ok"


def _stage3_circuit_breaker() -> Tuple[bool, str]:
    if TRADING_STAGE != 3:
        return True, "ok"
    daily_loss = read_json_cache("daily_loss.json")
    daily_value = float(daily_loss.get("daily", 0.0))
    balance = _extract_balance_usdt(get_balance())
    if balance <= 0:
        return False, "잔고 조회 실패로 진입 차단"
    daily_loss_ratio = abs(daily_value) / balance if daily_value < 0 else 0.0
    if daily_loss_ratio >= STAGE3_DAILY_LOSS_HARD_LIMIT:
        return False, f"Stage 3 서킷 브레이커: 일일 손실 {daily_loss_ratio:.1%} ≥ 15%"
    return True, "ok"


def _validate_pre_entry(
    side: str,
    sl_price: Optional[float],
    decision_price: float,
    last_price: float,
    acceptable_price_range: Optional[Dict[str, float]],
    requested_contracts: Optional[float],
    requested_leverage: Optional[float],
) -> Dict[str, Any]:
    # 1. SL 존재 확인
    if sl_price is None:
        return {"ok": False, "reason": "SL 필수"}

    # 2. 포지션 크기 계산 → 손실 ≤ 5% 검증
    balance = _extract_balance_usdt(get_balance())
    if balance <= 0:
        balance = 34.93
        logging.warning("잔고 조회 실패, 기본값 %.2f 사용", balance)

    ct_val, lot_sz = CT_VAL, LOT_SZ
    sized_contracts, risk_msg = _calc_contracts(balance, decision_price, float(sl_price), ct_val, lot_sz)

    final_contracts = sized_contracts
    notice = risk_msg.get("notice", "")
    if requested_contracts is not None and requested_contracts > sized_contracts:
        final_contracts = sized_contracts
        notice = f"요청 {requested_contracts}계약 → {sized_contracts}계약으로 축소됨"
    elif requested_contracts is not None:
        final_contracts = max(float(requested_contracts), 1.0)

    # 3. 동시 포지션 수 ≤ 1 확인
    if TRADING_STAGE == 1:
        paper_pos = read_json_cache("paper_position.json")
        pos_count = 1 if paper_pos.get("has_position") else 0
    else:
        pos_count = _extract_open_positions_count(get_positions(SYMBOL))
    if pos_count >= MAX_CONCURRENT_POSITIONS:
        return {"ok": False, "reason": "동시 포지션 제한 초과"}

    # 4. 레버리지 ≤ 10x 확인
    check_leverage = float(requested_leverage if requested_leverage is not None else LEVERAGE)
    if check_leverage > LEVERAGE:
        return {"ok": False, "reason": f"레버리지 제한 초과: {check_leverage} > {LEVERAGE}"}

    # 5. 슬리피지 검증
    slippage_ok, slippage_msg = _validate_slippage(decision_price, last_price, acceptable_price_range)
    if not slippage_ok:
        return {"ok": False, "reason": slippage_msg}

    # 6. Stage 3: 일일 누적 손실 15% 확인
    stage3_ok, stage3_msg = _stage3_circuit_breaker()
    if not stage3_ok:
        return {"ok": False, "reason": stage3_msg}

    return {
        "ok": True,
        "contracts": final_contracts,
        "balance": balance,
        "ct_val": ct_val,
        "lot_sz": lot_sz,
        "notice": notice,
        "side": side,
    }


def open_position(
    action: str,
    ord_type: str,
    sl_price: float,
    decision_price: float,
    last_price: float,
    tp_price: Optional[float] = None,
    acceptable_price_range: Optional[Dict[str, float]] = None,
    requested_contracts: Optional[float] = None,
    requested_leverage: Optional[float] = None,
    px: Optional[float] = None,
    atr: Optional[float] = None,
    market_env: Optional[Dict[str, Any]] = None,
    active_indicators: Optional[List[str]] = None,
    reason: str = "",
) -> Dict[str, Any]:
    side = "long" if action == "open_long" else "short"
    validated = _validate_pre_entry(
        side=side,
        sl_price=sl_price,
        decision_price=decision_price,
        last_price=last_price,
        acceptable_price_range=acceptable_price_range,
        requested_contracts=requested_contracts,
        requested_leverage=requested_leverage,
    )
    if not validated.get("ok"):
        return validated

    contracts = validated["contracts"]
    notice = validated.get("notice", "")

    if TRADING_STAGE == 1:
        opened = open_paper_position(
            side=side,
            entry_price=last_price,
            size=contracts,
            sl_price=sl_price,
            tp_price=tp_price,
            atr=atr,
            market_env=market_env,
            active_indicators=active_indicators,
        )
        opened["notice"] = notice
        return opened

    payload: Dict[str, Any] = {
        "instId": SYMBOL,
        "tdMode": "cross",
        "side": "buy" if side == "long" else "sell",
        "posSide": side,
        "ordType": ord_type,
        "sz": str(contracts),
        "clOrdId": _next_clordid(),
        "attachAlgoOrds": [
            {
                "slTriggerPx": str(sl_price),
                "slOrdPx": "-1",
                "slTriggerPxType": "last",
                **(
                    {
                        "tpTriggerPx": str(tp_price),
                        "tpOrdPx": "-1",
                        "tpTriggerPxType": "last",
                    }
                    if tp_price is not None
                    else {}
                ),
            }
        ],
    }
    if ord_type == "limit" and px is not None:
        payload["px"] = str(px)

    order_resp = place_order(payload)

    sl_ok = verify_sl_registration(timeout_sec=5)
    if not sl_ok:
        force_result = _force_close_if_needed(side=side, size=contracts)
        return {
            "ok": False,
            "reason": "SL 등록 실패로 강제 청산",
            "order": order_resp,
            "force_close": force_result,
            "telegram_alert": "SL 등록 실패로 강제 청산",
            "notice": notice,
        }

    return {"ok": True, "order": order_resp, "notice": notice}


def verify_sl_registration(timeout_sec: int = 5) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        pending = get_algo_orders_pending(ord_type="conditional", inst_type="SWAP")
        for row in pending.get("data", []):
            if row.get("instId") == SYMBOL and row.get("state") == "live":
                return True
        time.sleep(0.5)
    return False


def _force_close_if_needed(side: str, size: float) -> Dict[str, Any]:
    if TRADING_STAGE == 1:
        return close_paper_position(exit_price=0, close_percent=1.0, reason="sl_register_failed")

    close_side = "sell" if side == "long" else "buy"
    payload = {
        "instId": SYMBOL,
        "tdMode": "cross",
        "side": close_side,
        "posSide": side,
        "ordType": "market",
        "sz": str(size),
        "clOrdId": _next_clordid(),
        "reduceOnly": "true",
    }
    return place_order(payload)


def modify_sl(
    algo_id: str,
    side: str,
    current_sl: float,
    new_sl: float,
    tp_price: Optional[float] = None,
    sz: Optional[float] = None,
) -> Dict[str, Any]:
    # SL 방향 검증
    if side == "long" and new_sl < current_sl:
        return {"ok": False, "reason": "long 포지션은 SL 후퇴 금지"}
    if side == "short" and new_sl > current_sl:
        return {"ok": False, "reason": "short 포지션은 SL 후퇴 금지"}

    amend_payload = {
        "instId": SYMBOL,
        "algoId": algo_id,
        "newSlTriggerPx": str(new_sl),
    }
    amend_resp = amend_algos(amend_payload)
    if amend_resp.get("code") == "0":
        return {"ok": True, "result": amend_resp}

    cancel_resp = cancel_algos({"instId": SYMBOL, "algoId": [algo_id]})

    # 폴백: cancel 성공 후 강제 청산 + 텔레그램 알림
    # 독립 algo order 재등록은 현재 미지원 — 안전을 위해 포지션 청산
    logging.error("SL amend 실패 + 재등록 불가, 강제 청산 진행")
    force_result = _force_close_if_needed(side=side, size=float(sz or 1))
    return {
        "ok": False,
        "reason": "SL 수정 실패로 강제 청산",
        "amend": amend_resp,
        "cancel": cancel_resp,
        "force_close": force_result,
        "telegram_alert": "SL 수정 실패로 강제 청산",
    }
