from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import config

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

# === 주문 제어 상태 [v4.1] ===
_last_order_time = {}      # {"long": datetime, "short": datetime}
_cancel_retry_count = {}   # {"clOrdId": int}
_last_price_time = None    # datetime — WebSocket 가격 수신 시각 (외부에서 갱신)
_last_price = None         # float — 최신 가격 (외부에서 갱신)


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
    max_slip: float = MAX_ENTRY_SLIPPAGE,
) -> Tuple[bool, str]:
    if acceptable_price_range:
        min_px = float(acceptable_price_range.get("min", last_price))
        max_px = float(acceptable_price_range.get("max", last_price))
        if not (min_px <= last_price <= max_px):
            return False, f"가격 범위 초과로 진입 취소됨. 현재가 {last_price}"

    if decision_price <= 0:
        return False, "결정 가격이 유효하지 않음"

    drift = abs(last_price - decision_price) / decision_price
    if drift > max_slip:
        return False, f"슬리피지 {drift:.2%} > {max_slip:.2%}"
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
    action: str,
    side: str,
    sl_price: Optional[float],
    decision_price: float,
    last_price: float,
    acceptable_price_range: Optional[Dict[str, float]],
    requested_contracts: Optional[float],
    requested_leverage: Optional[float],
    market_env: Optional[Dict[str, Any]] = None,
    override_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    # 0. 가격 stale 체크 [v4.1]
    if _last_price_time is None:
        return {"ok": False, "approved": False, "reason": "price_data_unavailable"}
    age = (datetime.now(timezone.utc) - _last_price_time).total_seconds()
    if age > config.PRICE_STALE_SECONDS:
        return {"ok": False, "approved": False, "reason": "price_data_stale: {}s old".format(int(age))}

    # 0b. 주문 쿨다운 체크 [v4.1]
    direction = "long" if "long" in action else "short"
    if check_order_cooldown(direction):
        return {"ok": False, "approved": False, "reason": "order_cooldown: same direction within {}s".format(
            config.ORDER_COOLDOWN_SECONDS)}

    # 1. SL 존재 확인
    if sl_price is None:
        return {"ok": False, "approved": False, "reason": "SL 필수"}

    balance = _extract_balance_usdt(get_balance())
    if balance <= 0:
        balance = 34.93
        logging.warning("잔고 조회 실패, 기본값 %.2f 사용", balance)

    # 2. 포지션 사이징 — 리스크 5% 검증 [v4.1: 최소 1계약 강제 삭제]
    entry_price = float(last_price if last_price > 0 else decision_price)
    max_loss = balance * config.MAX_LOSS_PERCENT
    stop_distance = abs(entry_price - float(sl_price)) * CT_VAL
    if stop_distance == 0:
        return {"ok": False, "approved": False, "reason": "sl_price equals entry_price"}
    contracts_raw = max_loss / stop_distance
    contracts = math.floor(contracts_raw / LOT_SZ) * LOT_SZ
    if contracts < 1:
        return {"ok": False, "approved": False, "reason": "balance_too_low_for_risk: need wider SL or more balance"}

    final_contracts = contracts
    notice = ""
    if requested_contracts is not None and requested_contracts > contracts:
        final_contracts = contracts
        notice = f"요청 {requested_contracts}계약 → {contracts}계약으로 축소됨"
    elif requested_contracts is not None:
        final_contracts = max(float(requested_contracts), 1.0)

    # 3. 동시 포지션 수 ≤ 1 확인
    if TRADING_STAGE == 1:
        paper_pos = read_json_cache("paper_position.json")
        pos_count = 1 if paper_pos.get("has_position") else 0
    else:
        pos_count = _extract_open_positions_count(get_positions(SYMBOL))
    if pos_count >= MAX_CONCURRENT_POSITIONS:
        return {"ok": False, "approved": False, "reason": "동시 포지션 제한 초과"}

    # 4. 레버리지 ≤ 10x 확인
    check_leverage = float(requested_leverage if requested_leverage is not None else LEVERAGE)
    if check_leverage > LEVERAGE:
        return {"ok": False, "approved": False, "reason": f"레버리지 제한 초과: {check_leverage} > {LEVERAGE}"}

    # 5. 슬리피지 검증 [v4.1: 동적 완화]
    max_slip = config.MAX_ENTRY_SLIPPAGE
    if market_env and market_env.get("volatility") == "high":
        max_slip = config.MAX_ENTRY_SLIPPAGE_HIGH_VOL
    slippage_ok, slippage_msg = _validate_slippage(decision_price, last_price, acceptable_price_range, max_slip=max_slip)
    if not slippage_ok:
        return {"ok": False, "approved": False, "reason": slippage_msg}

    # 6. Stage 3: 일일 누적 손실 15% 확인
    stage3_ok, stage3_msg = _stage3_circuit_breaker()
    if not stage3_ok:
        return {"ok": False, "approved": False, "reason": stage3_msg}

    # 7. 소프트 가드레일 — 원칙 매칭 [v4.1]
    principle_result = _check_principle_match(action, market_env or {}, override_ids)
    if principle_result.get("blocked"):
        return {"ok": False, "approved": False, "reason": principle_result["reason"],
                "principle_id": principle_result.get("principle_id"),
                "soft_block": True}

    return {
        "ok": True,
        "approved": True,
        "contracts": final_contracts,
        "balance": balance,
        "ct_val": CT_VAL,
        "lot_sz": LOT_SZ,
        "notice": notice,
        "side": side,
        "principle_notifications": principle_result.get("notifications", []),
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
    override_principle_ids: Optional[List[str]] = None,
    reason: str = "",
) -> Dict[str, Any]:
    side = "long" if action == "open_long" else "short"
    validated = _validate_pre_entry(
        action=action,
        side=side,
        sl_price=sl_price,
        decision_price=decision_price,
        last_price=last_price,
        acceptable_price_range=acceptable_price_range,
        requested_contracts=requested_contracts,
        requested_leverage=requested_leverage,
        market_env=market_env,
        override_ids=override_principle_ids,
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
        if opened.get("ok"):
            record_order_time(side)
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

    record_order_time(side)
    return {"ok": True, "order": order_resp, "notice": notice, "principle_notifications": validated.get("principle_notifications", [])}


def update_price(price: float, timestamp: datetime = None):
    """WebSocket 콜백에서 호출. 최신 가격과 수신 시각을 갱신."""
    global _last_price, _last_price_time
    _last_price = price
    _last_price_time = timestamp or datetime.now(timezone.utc)


def _record_trigger(principle: dict, action_taken: str):
    """원칙의 trigger_history 업데이트."""
    th = principle.get("trigger_history", {})
    th["total_triggered"] = th.get("total_triggered", 0) + 1
    if action_taken in ("info_notified", "followed"):
        th["followed"] = th.get("followed", 0) + 1
    elif action_taken in ("overridden", "block_suggested"):
        th["ignored"] = th.get("ignored", 0) + 1
    principle["trigger_history"] = th

    from logger import read_json, write_json
    principles = read_json("principles.json")
    for i, p in enumerate(principles):
        if p.get("id") == principle.get("id"):
            principles[i] = principle
            break
    write_json("principles.json", principles)


def _check_principle_match(action: str, market_env: dict, override_ids: list = None) -> dict:
    """
    principles.json에서 현재 환경+행동 매칭 검색.
    Returns: {"blocked": bool, "reason": str, "principle_id": str, "notifications": list}
    """
    from logger import read_json
    principles = read_json("principles.json")
    override_ids = override_ids or []
    notifications = []

    for p in principles:
        tc = p.get("trigger_conditions", {})
        if not tc:
            continue

        env_match = tc.get("env_match", {})
        if env_match:
            if not all(market_env.get(k) == v for k, v in env_match.items()):
                continue

        action_match = tc.get("action_match", [])
        if action_match and action not in action_match:
            continue

        pid = p.get("id", "")
        cooloff = p.get("cooloff_remaining", 0)
        alert_level = p.get("alert_level", "info")

        if cooloff > 0:
            return {"blocked": True,
                    "reason": "principle {} in cooloff ({} remaining). Override not allowed.".format(pid, cooloff),
                    "principle_id": pid, "notifications": []}

        if pid in override_ids:
            _record_trigger(p, "overridden")
            notifications.append("principle {} overridden by request".format(pid))
            continue

        if alert_level == "info":
            _record_trigger(p, "info_notified")
            notifications.append("principle {} matched (info): {}".format(pid, p.get("content", "")))
            continue

        if alert_level == "block_suggest":
            _record_trigger(p, "block_suggested")
            return {"blocked": True,
                    "reason": "principle {} triggered: {}. Include override_principle_ids=[\"{}\"] to proceed.".format(
                        pid, p.get("content", ""), pid),
                    "principle_id": pid, "notifications": notifications}

    return {"blocked": False, "reason": "", "notifications": notifications}


def apply_cooloff_if_needed(trade_result: dict):
    """
    거래 종료 후 호출. override한 원칙의 결과가 <= COOLOFF_LOSS_THRESHOLD이면 쿨오프 적용.
    """
    from logger import read_json, write_json
    principle_triggered = trade_result.get("principle_triggered", [])
    pnl_r = trade_result.get("pnl_r", 0)

    if pnl_r > config.COOLOFF_LOSS_THRESHOLD:
        return

    principles = read_json("principles.json")
    updated = False
    for pt in principle_triggered:
        if pt.get("action_taken") == "overridden":
            pid = pt.get("principle_id", "")
            for p in principles:
                if p.get("id") == pid:
                    p["cooloff_remaining"] = config.COOLOFF_TRIGGER_COUNT
                    if p.get("alert_level") != "block_suggest":
                        p["alert_level"] = "block_suggest"
                    th = p.get("trigger_history", {})
                    outcomes = th.get("ignored_outcomes", [])
                    outcomes.append({
                        "trade_id": trade_result.get("id", ""),
                        "result": "loss",
                        "pnl_r": pnl_r
                    })
                    th["ignored_outcomes"] = outcomes
                    p["trigger_history"] = th
                    updated = True
    if updated:
        write_json("principles.json", principles)


def decrement_cooloff(principle_id: str):
    """원칙 트리거 시 쿨오프 카운트 1 감소."""
    from logger import read_json, write_json
    principles = read_json("principles.json")
    for p in principles:
        if p.get("id") == principle_id and p.get("cooloff_remaining", 0) > 0:
            p["cooloff_remaining"] -= 1
            write_json("principles.json", principles)
            return


def check_order_cooldown(direction: str) -> bool:
    """동일 방향 주문이 ORDER_COOLDOWN_SECONDS 이내면 True(차단)."""
    last = _last_order_time.get(direction)
    if last is None:
        return False
    elapsed = (datetime.now(timezone.utc) - last).total_seconds()
    return elapsed < config.ORDER_COOLDOWN_SECONDS


def record_order_time(direction: str):
    """주문 실행 후 시간 기록."""
    _last_order_time[direction] = datetime.now(timezone.utc)


def can_retry_cancel(cl_ord_id: str) -> bool:
    """취소 재시도 가능 여부. MAX_CANCEL_RETRIES 초과 시 False."""
    count = _cancel_retry_count.get(cl_ord_id, 0)
    if count >= config.MAX_CANCEL_RETRIES:
        return False
    _cancel_retry_count[cl_ord_id] = count + 1
    return True


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
