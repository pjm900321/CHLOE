from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import requests

import config
from ai_brain import AIBrain
from config import ALERT_THROTTLE_SECONDS, SYMBOL, TELEGRAM_POLL_INTERVAL
from config_secret import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from data import get_positions, get_ticker, place_order
from logger import read_json_cache, write_json_cache
from paper_trading import close_paper_position


class TelegramHandler:
    def __init__(self, ai_brain: Optional[AIBrain] = None) -> None:
        self.ai_brain = ai_brain or AIBrain()
        self.base_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
        self.offset: Optional[int] = None
        self._last_error_sent: Dict[str, float] = {}
        self.muted_until: Optional[datetime] = None
        self.shutdown_requested = False

    def poll_loop(self) -> None:
        while True:
            try:
                self._poll_once()
            except Exception as exc:
                self.send_message(f"telegram poll error: {exc}", is_error=True)
            time.sleep(TELEGRAM_POLL_INTERVAL)

    def _poll_once(self) -> None:
        params: Dict[str, Any] = {"timeout": TELEGRAM_POLL_INTERVAL}
        if self.offset is not None:
            params["offset"] = self.offset

        resp = requests.get(f"{self.base_url}/getUpdates", params=params, timeout=10)
        resp.raise_for_status()
        updates = resp.json().get("result", [])

        for upd in updates:
            self.offset = int(upd["update_id"]) + 1
            msg = upd.get("message", {})
            text = msg.get("text", "")
            chat_id = str(msg.get("chat", {}).get("id", ""))
            if TELEGRAM_CHAT_ID and chat_id and chat_id != str(TELEGRAM_CHAT_ID):
                continue
            self._handle_text(text)

    def _handle_text(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return

        if text.startswith("/"):
            self._handle_command(text)
            return

        self.ai_brain.run_trigger_async("user_message", text)
        self.send_message("메시지를 CHLOE에 전달했습니다.")

    def _handle_command(self, cmd_text: str) -> None:
        parts = cmd_text.split()
        cmd = parts[0].lower()

        if cmd == "/status":
            self.send_message(self._status_text())
        elif cmd == "/summary":
            self.send_message(self._summary_text())
        elif cmd == "/panic":
            self.send_message(self._panic())
        elif cmd == "/pause":
            self.send_message(self._pause("user_pause"))
        elif cmd == "/resume":
            self.send_message(self._resume())
        elif cmd == "/stop":
            self.send_message(self._stop())
        elif cmd == "/cost":
            self.send_message(self._cost_text())
        elif cmd == "/confirm_stage2":
            self.send_message(self._handle_confirm_stage2())
        elif cmd == "/confirm_stage3":
            self.send_message(self._handle_confirm_stage3())
        elif cmd == "/mute":
            hours = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
            self.muted_until = datetime.now(timezone.utc) + timedelta(hours=hours)
            self.send_message(f"{hours}시간 동안 일반 알림을 무음 처리했습니다.")
        else:
            self.send_message(f"알 수 없는 명령어: {cmd}")

    def _status_text(self) -> str:
        ticker = get_ticker(SYMBOL)
        tdata = ticker.get("data", [{}])[0] if ticker.get("data") else {}
        price = float(tdata.get("last", 0.0))

        paper_pos = read_json_cache("paper_position.json")
        performance = read_json_cache("performance.json")
        scenarios = read_json_cache("scenarios.json")
        alerts = read_json_cache("alerts.json")
        analysis = read_json_cache("current_analysis.json")

        unrealized = paper_pos.get("unrealized_pnl", 0.0) if isinstance(paper_pos, dict) else 0.0
        next_alert = "없음"
        if isinstance(alerts, list) and alerts:
            first = alerts[0]
            next_alert = f"{first.get('price')} ({first.get('direction')})"

        return (
            f"현재가: {price}\n"
            f"미실현 손익: {unrealized}\n"
            f"오늘/주간 손익: {performance.get('daily_pnl', 0)}/{performance.get('weekly_pnl', 0)}\n"
            f"활성 시나리오 수: {len(scenarios) if isinstance(scenarios, list) else 0}\n"
            f"다음 알림: {next_alert}\n"
            f"마지막 분석 시각: {analysis.get('updated_at', '없음') if isinstance(analysis, dict) else '없음'}"
        )

    def _summary_text(self) -> str:
        perf = read_json_cache("performance.json")
        return (
            f"일간/주간 PnL: {perf.get('daily_pnl', 0)}/{perf.get('weekly_pnl', 0)}\n"
            f"승률: {perf.get('win_rate', 0)}\n"
            f"EV: {perf.get('ev', 0)}\n"
            f"총 거래 횟수: {perf.get('total_trades', 0)}"
        )

    def _market_snapshot(self) -> Dict[str, Any]:
        ticker = get_ticker(SYMBOL)
        tdata = ticker.get("data", [{}])[0] if ticker.get("data") else {}
        current_analysis = read_json_cache("current_analysis.json")
        env = current_analysis.get("market_environment", {}) if isinstance(current_analysis, dict) else {}
        return {
            "price": float(tdata.get("last", 0.0)),
            "trend": env.get("trend", "unknown"),
            "volatility": env.get("volatility", "unknown"),
        }

    def _pause(self, reason: str) -> str:
        state = read_json_cache("system_state.json")
        state.update(
            {
                "paused": True,
                "pause_reason": reason,
                "pause_time": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                "market_snapshot": self._market_snapshot(),
            }
        )
        write_json_cache("system_state.json", state)
        return "시스템을 일시정지했습니다. 새 진입은 중단되며 기존 포지션은 유지됩니다."

    def _panic(self) -> str:
        close_report = ""
        if config.TRADING_STAGE == 1:
            res = close_paper_position(exit_price=self._market_snapshot()["price"], close_percent=1.0, reason="panic")
            close_report = f"Stage1 청산 결과: {res.get('ok')}"
        else:
            pos = get_positions(SYMBOL)
            for row in pos.get("data", []):
                size = abs(float(row.get("pos", 0.0)))
                if size <= 0:
                    continue
                side = "sell" if row.get("posSide") == "long" else "buy"
                place_order(
                    {
                        "instId": SYMBOL,
                        "tdMode": "cross",
                        "side": side,
                        "posSide": row.get("posSide", "long"),
                        "ordType": "market",
                        "sz": str(size),
                    }
                )
            close_report = "Stage2/3 시장가 청산 명령 전송"

        self._pause("user_panic")
        return f"PANIC 실행 완료. {close_report}"

    def _handle_confirm_stage2(self) -> str:
        """Stage 2 전환 수동 승인."""
        from logger import read_json

        performance = read_json("performance.json")
        total = performance.get("total_trades", 0)
        ev = performance.get("ev", 0)
        mdd = performance.get("max_drawdown", 0)

        if config.TRADING_STAGE != 1:
            return f"현재 Stage {config.TRADING_STAGE}입니다. Stage 1에서만 Stage 2로 전환 가능합니다."

        errors = []
        if total < config.MIN_TRADES_FOR_TRANSITION:
            errors.append(f"거래 횟수: {total}/{config.MIN_TRADES_FOR_TRANSITION}")
        if ev <= 0:
            errors.append(f"EV: {ev:.4f} (양수 필요)")
        if mdd > config.MAX_MDD_FOR_TRANSITION:
            errors.append(f"MDD: {mdd:.1%} (최대 {config.MAX_MDD_FOR_TRANSITION:.0%})")

        trade_log = read_json("trade_log.json")
        if trade_log:
            first_trade = trade_log[0].get("entry_time", "")
            if first_trade:
                try:
                    first_dt = datetime.fromisoformat(first_trade.replace("Z", "+00:00"))
                    days = (datetime.now(timezone.utc) - first_dt).days
                    if days < config.MIN_DAYS_FOR_TRANSITION:
                        errors.append(f"운영 일수: {days}/{config.MIN_DAYS_FOR_TRANSITION}")
                except Exception:
                    errors.append("운영 일수 확인 불가")
        else:
            errors.append("거래 이력 없음")

        if errors:
            return "Stage 2 전환 조건 미달:\n" + "\n".join(errors)

        config.TRADING_STAGE = 2
        return "Stage 2 (OKX Demo)로 전환 완료. 다음 재시작 시 config.py의 TRADING_STAGE를 2로 수정하세요."

    def _handle_confirm_stage3(self) -> str:
        """Stage 3 전환 수동 승인."""
        from logger import read_json

        performance = read_json("performance.json")
        principles = read_json("principles.json")

        if config.TRADING_STAGE != 2:
            return f"현재 Stage {config.TRADING_STAGE}입니다. Stage 2에서만 Stage 3으로 전환 가능합니다."

        errors = []
        total = performance.get("total_trades", 0)
        if total < config.MIN_TRADES_FOR_TRANSITION:
            errors.append(f"거래 횟수: {total}/{config.MIN_TRADES_FOR_TRANSITION}")
        if performance.get("ev", 0) <= 0:
            errors.append("EV 양수 필요")
        if performance.get("max_drawdown", 0) > config.MAX_MDD_FOR_TRANSITION:
            errors.append("MDD 초과")

        risk_principles = [
            p
            for p in principles
            if "risk" in p.get("content", "").lower()
            or p.get("trigger_conditions", {}).get("env_match", {}).get("volatility")
        ]
        if not risk_principles:
            errors.append("principles.json에 리스크 관련 원칙 없음")

        trade_log = read_json("trade_log.json")
        if trade_log:
            first_trade = trade_log[0].get("entry_time", "")
            if first_trade:
                try:
                    first_dt = datetime.fromisoformat(first_trade.replace("Z", "+00:00"))
                    days = (datetime.now(timezone.utc) - first_dt).days
                    if days < config.MIN_DAYS_FOR_TRANSITION:
                        errors.append(f"운영 일수: {days}/{config.MIN_DAYS_FOR_TRANSITION}")
                except Exception:
                    pass

        if errors:
            return "Stage 3 전환 조건 미달:\n" + "\n".join(errors)

        config.TRADING_STAGE = 3
        return "Stage 3 (Live)로 전환 완료. 서킷 브레이커 활성화됨. config.py의 TRADING_STAGE를 3으로 수정하세요."

    def _resume(self) -> str:
        state = read_json_cache("system_state.json")
        state["paused"] = False
        state["resume_time"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        write_json_cache("system_state.json", state)
        return "pause/panic 상태를 해제했습니다."

    def _stop(self) -> str:
        state = read_json_cache("system_state.json")
        paper_pos = read_json_cache("paper_position.json")
        if isinstance(paper_pos, dict) and paper_pos.get("has_position"):
            return "경고: 열린 포지션이 있습니다. SL/TP는 OKX에 등록돼 있으나 CHLOE가 관리 불가. 트레일링 스톱/부분 청산 불가."
        state["shutdown"] = "normal"
        state["shutdown_time"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        write_json_cache("system_state.json", state)
        self.shutdown_requested = True
        return "정상 종료 플래그를 기록했습니다. 시스템이 종료됩니다."

    def _cost_text(self) -> str:
        now = time.time()
        today_calls = len([ts for ts in self.ai_brain._call_timestamps if now - ts <= 86400])
        month_calls = len([ts for ts in self.ai_brain._call_timestamps if now - ts <= 30 * 86400])
        # 정확 비용은 토큰 로그 누적 후 계산. 여기서는 추정치 자리표시자.
        est_cost = today_calls * 0.01
        return f"오늘 호출: {today_calls}, 이번 달 호출: {month_calls}, 추정 비용: ${est_cost:.2f}"

    def send_message(self, text: str, is_error: bool = False, is_critical: bool = False) -> bool:
        if not text:
            return False

        now = time.time()
        if is_error:
            last_sent = self._last_error_sent.get(text)
            if last_sent is not None and (now - last_sent) < ALERT_THROTTLE_SECONDS:
                return False
            self._last_error_sent[text] = now

        if not is_critical and not is_error and self.muted_until and datetime.now(timezone.utc) < self.muted_until:
            return False

        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
        }
        try:
            r = requests.post(f"{self.base_url}/sendMessage", json=payload, timeout=10)
            r.raise_for_status()
            return True
        except Exception as exc:
            logging.error("send_message failed: %s", exc)
            return False

    def send_log_file(self, filepath: str) -> bool:
        if not filepath or not Path(filepath).exists():
            return False
        try:
            with open(filepath, "rb") as fp:
                files = {"document": fp}
                data = {"chat_id": TELEGRAM_CHAT_ID}
                r = requests.post(f"{self.base_url}/sendDocument", data=data, files=files, timeout=20)
                r.raise_for_status()
            return True
        except Exception as exc:
            logging.error("send_log_file failed: %s", exc)
            return False


_default_handler: Optional[TelegramHandler] = None


def get_handler() -> TelegramHandler:
    global _default_handler
    if _default_handler is None:
        _default_handler = TelegramHandler()
    return _default_handler


def send_message(text: str, is_error: bool = False, is_critical: bool = False) -> bool:
    return get_handler().send_message(text, is_error=is_error, is_critical=is_critical)


def send_log_file(filepath: str) -> bool:
    return get_handler().send_log_file(filepath)
