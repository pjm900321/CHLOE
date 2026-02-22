from __future__ import annotations

import logging
import heapq
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

import requests

import config
from ai_brain import AIBrain, TRIGGER_PRIORITY
from config import (
    COLD_STORE_DAYS,
    HEARTBEAT_INTERVAL,
    POSITION_CHECK_INTERVAL,
    SHEETS_SYNC_INTERVAL,
    STATS_REBUILD_INTERVAL,
    SYMBOL,
)
from data import get_positions
from logger import daily_stats_rebuild, read_json, read_json_cache, sync_to_sheets, write_json_cache
from paper_trading import check_paper_sl_tp
from telegram_handler import TelegramHandler, send_message

UPTIMEROBOT_URL = ""


class Scheduler:
    def __init__(self, ai_brain: Optional[AIBrain] = None, telegram_handler: Optional[TelegramHandler] = None) -> None:
        self.ai_brain = ai_brain or AIBrain()
        self.telegram_handler = telegram_handler or TelegramHandler(ai_brain=self.ai_brain)

        self.last_price: float = 0.0
        self.last_claude_call_at: Optional[float] = None
        self.pending_nonurgent: Set[str] = set()

        now = time.time()
        self._last_heartbeat_at = now
        self._last_sheets_sync_at = now
        self._last_position_check_at = now
        self._last_cold_store_at = now
        self._last_periodic_review_at = now

        self._last_position_seen_nonzero = False
        self._position_missing_notified_at: Optional[float] = None
        self._last_limit_check_at = now

        self._resume_notified = False
        self._last_loss_alert_trade = ""
        self._last_daily_loss_alert = ""
        self._trigger_queue: List[tuple[int, str, Dict[str, Any]]] = []

    def on_price_update(self, price: float) -> None:
        self.last_price = price
        # executor에 최신 가격 전달 [v4.1 P1 fix]
        from executor import update_price
        update_price(price)

        if config.TRADING_STAGE == 1:
            try:
                check_paper_sl_tp(current_price=price)
            except Exception as exc:
                logging.error("check_paper_sl_tp failed: %s", exc)

        self._check_alert_trigger(price)
        self._check_stats_rebuild_and_meta()

    def on_order_update(self, data: Dict[str, Any]) -> None:
        logging.info("order update: %s", data)

    def on_position_update(self, data: Dict[str, Any]) -> None:
        logging.info("position update: %s", data)
        rows = data.get("data", []) if isinstance(data, dict) else []
        has_position = any(abs(float(row.get("pos", 0.0))) > 0 for row in rows)

        if self._last_position_seen_nonzero and not has_position:
            self._queue_trigger("trade_closed", {"desc": "포지션 종료 감지"})
            self._check_loss_alerts()
        self._last_position_seen_nonzero = has_position

    def tick(self) -> None:
        now = time.time()
        self._check_resume_notification()
        self._check_periodic_triggers(now)
        self._check_nonurgent_batching(now)
        self._check_heartbeat(now)
        self._check_sheets_sync(now)
        self._check_cold_store(now)
        self._check_stage23_position_poll(now)
        self._check_pending_limit_orders(now)
        self._process_trigger_queue()


    def run(self) -> None:
        while True:
            try:
                if self.telegram_handler.shutdown_requested:
                    logging.info("shutdown requested by telegram /stop")
                    return
                self.tick()
            except Exception as exc:
                logging.exception("scheduler run error: %s", exc)
            time.sleep(1)

    def run_forever(self) -> None:
        while True:
            try:
                self.tick()
                if self.telegram_handler.shutdown_requested:
                    logging.info("shutdown requested by telegram /stop")
                    return
            except Exception as exc:
                logging.exception("scheduler tick error: %s", exc)
            time.sleep(1)

    def _is_paused(self) -> bool:
        state = read_json_cache("system_state.json")
        return bool(state.get("paused", False)) if isinstance(state, dict) else False

    def _trigger_async(self, trigger: str, desc: str) -> None:
        if self._is_paused() and trigger in {"alert_triggered", "periodic_review", "meta_check", "first_analysis"}:
            logging.info("paused=true, skip trigger=%s", trigger)
            return
        self.ai_brain.run_trigger_async(trigger, desc, last_price=self.last_price if self.last_price > 0 else None)
        self.last_claude_call_at = time.time()

    def _queue_trigger(self, trigger_type: str, data: Optional[Dict[str, Any]] = None) -> None:
        priority = TRIGGER_PRIORITY.get(trigger_type, 99)
        heapq.heappush(self._trigger_queue, (priority, trigger_type, data or {}))

    def _next_trigger(self) -> Optional[tuple[int, str, Dict[str, Any]]]:
        if self._trigger_queue:
            return heapq.heappop(self._trigger_queue)
        return None

    def _process_trigger_queue(self) -> None:
        item = self._next_trigger()
        if not item:
            return
        _, trigger_type, data = item
        desc = data.get("desc") or data.get("text") or str(data)
        self._trigger_async(trigger_type, desc)

    def _should_run_periodic_review(self) -> bool:
        """활성 시나리오 또는 포지션이 있을 때만 periodic_review 실행."""
        scenarios = read_json("scenarios.json")
        has_scenarios = bool(scenarios)

        paper_pos = read_json("paper_position.json")
        has_paper_pos = bool(isinstance(paper_pos, dict) and paper_pos.get("has_position", False))

        has_real_pos = False
        if config.TRADING_STAGE >= 2:
            try:
                positions = get_positions()
                rows = positions.get("data", []) if isinstance(positions, dict) else positions
                has_real_pos = bool(rows)
            except Exception:
                pass

        return has_scenarios or has_paper_pos or has_real_pos

    def _check_loss_alerts(self) -> None:
        """연속 3패 또는 일일 10% 손실 시 immediate_review 트리거."""
        trade_log = read_json("trade_log.json")
        if not trade_log:
            return

        recent = trade_log[-config.CONSECUTIVE_LOSS_ALERT :]
        if len(recent) >= config.CONSECUTIVE_LOSS_ALERT:
            all_loss = all(t.get("pnl_usdt", 0) < 0 for t in recent)
            if all_loss:
                last_trade_id = recent[-1].get("id", "")
                if self._last_loss_alert_trade != last_trade_id:
                    self._last_loss_alert_trade = last_trade_id
                    self._queue_trigger(
                        "immediate_review",
                        {"reason": f"consecutive_{config.CONSECUTIVE_LOSS_ALERT}_losses"},
                    )
                    return

        daily_loss = read_json("daily_loss.json")
        daily_pnl = daily_loss.get("daily", 0) if isinstance(daily_loss, dict) else 0
        if daily_pnl < 0:
            try:
                from data import get_balance

                balance_data = get_balance()
                balance = float(balance_data.get("data", [{}])[0].get("totalEq", 34.93))
            except Exception:
                balance = 34.93
            loss_pct = abs(daily_pnl) / balance if balance > 0 else 0
            if loss_pct >= config.DAILY_LOSS_ALERT_PERCENT:
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if self._last_daily_loss_alert != today:
                    self._last_daily_loss_alert = today
                    self._queue_trigger("immediate_review", {"reason": f"daily_loss_{loss_pct:.1%}"})

    def _check_pending_limit_orders(self, now: float) -> None:
        """미체결 지정가 주문이 LIMIT_ORDER_EXPIRY_MINUTES 초과 시 자동 취소."""
        if now - self._last_limit_check_at < 60:
            return
        self._last_limit_check_at = now

        if config.TRADING_STAGE < 2:
            return

        from data import cancel_algos, get_algo_orders_pending

        try:
            pending = get_algo_orders_pending()
            rows = pending.get("data", []) if isinstance(pending, dict) else pending
            if not rows:
                return

            now_dt = datetime.now(timezone.utc)
            expiry = timedelta(minutes=config.LIMIT_ORDER_EXPIRY_MINUTES)
            for order in rows:
                created = order.get("cTime", "")
                if not created:
                    continue
                try:
                    created_dt = datetime.fromtimestamp(int(created) / 1000, tz=timezone.utc)
                except (ValueError, TypeError):
                    continue

                if now_dt - created_dt > expiry:
                    order_id = order.get("algoId", "")
                    if not order_id:
                        continue
                    from executor import can_retry_cancel

                    if can_retry_cancel(order_id):
                        try:
                            cancel_algos(order_id)
                            self._queue_trigger(
                                "user_message",
                                {
                                    "text": f"[시스템] 지정가 주문 {order_id} 만료 취소됨 ({config.LIMIT_ORDER_EXPIRY_MINUTES}분 초과)"
                                },
                            )
                        except Exception as exc:
                            logging.warning("지정가 주문 취소 실패: %s", exc)
        except Exception as exc:
            logging.warning("미체결 주문 확인 실패: %s", exc)

    def _get_rest_fallback_interval(self) -> int:
        """REST 폴백 폴링 간격. 포지션 보유 시 5초, 아닌 경우 30초."""
        paper = read_json("paper_position.json")
        if isinstance(paper, dict) and paper.get("has_position", False):
            return 5
        if config.TRADING_STAGE >= 2:
            try:
                positions = get_positions()
                rows = positions.get("data", []) if isinstance(positions, dict) else positions
                if rows:
                    return 5
            except Exception:
                pass
        return 30

    def _check_resume_notification(self) -> None:
        state = read_json_cache("system_state.json")
        if not isinstance(state, dict):
            return
        if state.get("paused"):
            self._resume_notified = False
            return
        if self._resume_notified:
            return
        if not state.get("resume_time"):
            return

        pause_time = state.get("pause_time", "")
        snapshot = state.get("market_snapshot", {}) if isinstance(state.get("market_snapshot"), dict) else {}
        pause_price = snapshot.get("price", 0)
        current_price = self.last_price

        self._trigger_async(
            "user_message",
            f"시스템이 재개되었습니다. 정지 기간: {pause_time} ~ {state.get('resume_time')}. 정지 시점 가격: {pause_price}, 현재 가격: {current_price}",
        )
        self._resume_notified = True

    def _check_alert_trigger(self, price: float) -> None:
        if self._is_paused():
            return
        alerts = read_json_cache("alerts.json")
        if not isinstance(alerts, list) or not alerts:
            return

        remained: List[Dict[str, Any]] = []
        triggered: List[Dict[str, Any]] = []
        for alert in alerts:
            alert_price = float(alert.get("price", 0.0))
            direction = alert.get("direction")
            hit = (direction == "above" and price >= alert_price) or (direction == "below" and price <= alert_price)
            if hit:
                triggered.append(alert)
            else:
                remained.append(alert)

        if triggered:
            write_json_cache("alerts.json", remained)
            for item in triggered:
                self._trigger_async(
                    "alert_triggered",
                    f"alert_id={item.get('id', 'n/a')} price={price} scenario={item.get('scenario_id', '')}",
                )

    def _check_periodic_triggers(self, now: float) -> None:
        if self._is_paused():
            return

        # periodic_review: 4시간마다
        if now - self._last_periodic_review_at >= 14400:
            if self._should_run_periodic_review():
                self._last_periodic_review_at = now
                self.pending_nonurgent.add("periodic_review")
            else:
                self._last_periodic_review_at = now

    def _check_nonurgent_batching(self, now: float) -> None:
        if not self.pending_nonurgent:
            return

        since_last = None if self.last_claude_call_at is None else now - self.last_claude_call_at

        # 마지막 Claude 호출 후 2~3분 이내 비긴급 트리거 배칭 실행
        if since_last is not None and since_last <= 180:
            batch = sorted(self.pending_nonurgent)
            self.pending_nonurgent.clear()
            for trig in batch:
                self._trigger_async(trig, "batched_nonurgent")
            return

        # 5분 내 호출 없었으면 단독 실행
        if since_last is None or since_last > 300:
            trig = sorted(self.pending_nonurgent)[0]
            self.pending_nonurgent.remove(trig)
            self._trigger_async(trig, "single_nonurgent_after_idle")

    def _check_stats_rebuild_and_meta(self) -> None:
        perf = read_json_cache("performance.json")
        last_rebuild_raw = perf.get("last_rebuild", "") if isinstance(perf, dict) else ""
        now_dt = datetime.now(timezone.utc)

        should_run = False
        if not last_rebuild_raw:
            should_run = True
        else:
            try:
                last_rebuild_dt = datetime.fromisoformat(str(last_rebuild_raw).replace("Z", "+00:00")).astimezone(timezone.utc)
                should_run = (now_dt - last_rebuild_dt).total_seconds() >= STATS_REBUILD_INTERVAL
            except Exception:
                should_run = True

        if should_run:
            daily_stats_rebuild()
            # 같은 iteration에서 meta_check 실행
            self._trigger_async("meta_check", "stats_rebuild_completed")

    def _check_heartbeat(self, now: float) -> None:
        if now - self._last_heartbeat_at < HEARTBEAT_INTERVAL:
            return
        self._last_heartbeat_at = now

        send_message("시스템 정상 가동")
        if UPTIMEROBOT_URL:
            try:
                requests.get(UPTIMEROBOT_URL, timeout=10)
            except Exception as exc:
                logging.warning("heartbeat GET failed: %s", exc)

    def _check_sheets_sync(self, now: float) -> None:
        if now - self._last_sheets_sync_at < SHEETS_SYNC_INTERVAL:
            return
        self._last_sheets_sync_at = now
        ok = sync_to_sheets()
        if not ok:
            logging.warning("Sheets sync failed")

    def _check_cold_store(self, now: float) -> None:
        if now - self._last_cold_store_at < 86400:
            return
        self._last_cold_store_at = now

        insights = read_json_cache("insights.json")
        cold = read_json_cache("cold_insights.json")
        if not isinstance(insights, list) or not isinstance(cold, list):
            return

        keep: List[Dict[str, Any]] = []
        moved: List[Dict[str, Any]] = []
        threshold = datetime.now(timezone.utc) - timedelta(days=COLD_STORE_DAYS)

        for item in insights:
            last_used = str(item.get("last_used_at", ""))
            try:
                dt = datetime.fromisoformat(last_used.replace("Z", "+00:00")).astimezone(timezone.utc)
            except Exception:
                keep.append(item)
                continue
            if dt < threshold:
                moved.append(item)
            else:
                keep.append(item)

        if moved:
            cold.extend(moved)
            write_json_cache("insights.json", keep)
            write_json_cache("cold_insights.json", cold)
            logging.info("cold store moved=%s", len(moved))

    def _check_stage23_position_poll(self, now: float) -> None:
        if config.TRADING_STAGE == 1:
            return
        if now - self._last_position_check_at < POSITION_CHECK_INTERVAL:
            return
        self._last_position_check_at = now

        try:
            positions = get_positions(SYMBOL)
            rows = positions.get("data", [])
            has_position = any(abs(float(row.get("pos", 0.0))) > 0 for row in rows)

            if self._last_position_seen_nonzero and not has_position:
                # 포지션 소실 대처(섹션 10-5): 알림 → 5분 대기 → 자동 처리 트리거
                if self._position_missing_notified_at is None:
                    self._position_missing_notified_at = now
                    send_message(
                        "포지션이 사라졌습니다 — 직접 정리하셨나요? 5분 내 응답 없으면 자동으로 OKX 이력 조회를 진행합니다.",
                        is_error=True,
                        is_critical=True,
                    )
                elif now - self._position_missing_notified_at >= 300:
                    self._queue_trigger(
                        "trade_closed",
                        {"desc": "포지션 소실 감지 후 5분 무응답. OKX 이력 확인 및 거래 기록 업데이트 필요."},
                    )
                    self._check_loss_alerts()
                    self._position_missing_notified_at = None
            else:
                self._position_missing_notified_at = None

            self._last_position_seen_nonzero = has_position
        except Exception as exc:
            logging.error("stage2/3 position poll failed: %s", exc)
