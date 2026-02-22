from __future__ import annotations

import importlib
import json
import logging
import threading
import time
import time as _time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import config

from config import (
    CLAUDE_MODEL,
    EXEMPT_TRIGGERS,
    MAX_CLAUDE_CALLS_PER_HOUR,
    MAX_ITERATIONS,
    SYMBOL,
    TRADING_STAGE,
)
from config_secret import CLAUDE_API_KEY
from data import get_balance, get_candles, get_ticker
from executor import modify_sl, open_position
from indicators import calculate_indicators
from logger import read_json_cache, write_json_cache, read_json, write_json, validate_insight, validate_principle
from paper_trading import close_paper_position, update_paper_sl, update_paper_tp
from tools import TOOLS


PART_A_PROMPT = """너는 CHLOE(클로이). 자율적으로 학습하고 성장하는 BTC 트레이더 AI.

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

[학습 원칙]
- 인사이트 작성 시 근거 거래 ID, 핵심 관찰, 실패 경험 필수.
- 원칙 작성 시 트리거 조건(환경/행동) 필수.
- Python 경고 태그를 진지하게 검토.
- 자신이 세운 원칙이 트리거되면, 무시할 명확한 근거 없는 한 따른다.
  무시 시 반드시 근거를 거래 메모에 기록.

[사용자 초기 힌트]
"횡보는 단순 수평이 아니라 내부 구조가 있다 — RSI 흐름, 거래량 변화,
횡보의 기울기를 관찰. 돌파 후 횡보가 이어지면 진짜 돌파, 즉시 되돌림은
페이크아웃 가능성이 높다."
→ 이것은 힌트일 뿐. 직접 검증하고 발전시킬 것."""

PART_B_PROMPT = """[분석 절차]
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

[학습 품질 기준]
- 샘플 3건 미만: 인사이트 불가, 관찰 메모로만 기록.
- 상세 맥락 필요 시 get_insight_detail 사용.
- meta_check 시 학습 건강 보고서 검토.
- 인사이트 폐기 시 save_insight(action="invalidate")로 사유 기록."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _json_or_text(data: Any) -> str:
    if data is None:
        return "없음"
    if isinstance(data, (dict, list)):
        if not data:
            return "없음"
        return json.dumps(data, ensure_ascii=False)
    return str(data)


# === insight_detail 캐시 [v4.1] ===
_insight_cache = {}  # {insight_id: {"data": dict, "expires": float}}

TRIGGER_PRIORITY = {
    "alert_triggered": 0,
    "immediate_review": 1,
    "trade_closed": 2,
    "user_message": 3,
    "periodic_review": 4,
    "meta_check": 5,
    "first_analysis": 0,
}


def _get_cached_insight(insight_id: str):
    """캐시에서 인사이트 상세 조회. TTL 초과 시 None."""
    entry = _insight_cache.get(insight_id)
    if entry and _time.time() < entry["expires"]:
        return entry["data"]
    return None


def _cache_insight(insight_id: str, data: dict):
    """인사이트 상세를 캐시에 저장."""
    _insight_cache[insight_id] = {
        "data": data,
        "expires": _time.time() + config.INSIGHT_DETAIL_CACHE_TTL
    }


class AIBrain:
    def __init__(self) -> None:
        self._call_timestamps: List[float] = []

    def run_trigger_async(self, trigger_type: str, trigger_description: str, last_price: Optional[float] = None) -> threading.Thread:
        thread = threading.Thread(
            target=self._run_trigger_with_retry,
            kwargs={
                "trigger_type": trigger_type,
                "trigger_description": trigger_description,
                "last_price": last_price,
            },
            daemon=True,
        )
        thread.start()
        return thread

    def _run_trigger_with_retry(self, trigger_type: str, trigger_description: str, last_price: Optional[float]) -> Dict[str, Any]:
        for attempt in range(1, 4):
            try:
                return self.run_trigger(trigger_type, trigger_description, last_price=last_price)
            except Exception as exc:
                logging.exception("Claude 호출 실패 (%s/3): %s", attempt, exc)
                if attempt == 3:
                    logging.error("Claude 3회 실패. 트리거 건너뜀 + 텔레그램 알림 필요")
                    return {
                        "ok": False,
                        "reason": "Claude 3회 실패",
                        "telegram_alert": "Claude 호출 3회 실패로 트리거 건너뜀",
                    }
                time.sleep(30)
        return {"ok": False, "reason": "unreachable"}

    def _enforce_hourly_limit(self, trigger_type: str) -> bool:
        if trigger_type in EXEMPT_TRIGGERS:
            return True
        now = time.time()
        one_hour_ago = now - 3600
        self._call_timestamps = [ts for ts in self._call_timestamps if ts >= one_hour_ago]
        if len(self._call_timestamps) >= MAX_CLAUDE_CALLS_PER_HOUR:
            return False
        return True

    def _record_call(self) -> None:
        self._call_timestamps.append(time.time())

    def _build_part_c(self, trigger_type: str, trigger_description: str, last_price: Optional[float]) -> str:
        ticker = get_ticker(SYMBOL)
        tdata = ticker.get("data", [{}])[0] if ticker.get("data") else {}
        price = float(last_price if last_price is not None else tdata.get("last", 0.0))
        change_24h = float(tdata.get("sodUtc8", 0.0))
        if price > 0 and change_24h > 0:
            change_24h = ((price - change_24h) / change_24h) * 100
        else:
            change_24h = 0.0

        current_analysis = read_json_cache("current_analysis.json")
        market_env = current_analysis.get("market_environment", {}) if isinstance(current_analysis, dict) else {}

        paper_position = read_json_cache("paper_position.json")
        if isinstance(paper_position, dict) and paper_position.get("has_position"):
            position_summary = _json_or_text({
                "side": paper_position.get("side"),
                "entry_price": paper_position.get("entry_price"),
                "size": paper_position.get("size"),
                "sl_price": paper_position.get("sl_price"),
                "tp_price": paper_position.get("tp_price"),
            })
        else:
            position_summary = "없음"

        balance_resp = get_balance()
        balance = 0.0
        try:
            details = balance_resp.get("data", [])[0].get("details", [])
            for row in details:
                if row.get("ccy") == "USDT":
                    balance = float(row.get("availEq") or row.get("availBal") or row.get("cashBal") or 0.0)
                    break
        except Exception:
            balance = 0.0

        scenarios = read_json_cache("scenarios.json")
        alerts = read_json_cache("alerts.json")
        principles = read_json_cache("principles.json")
        trades = read_json_cache("trade_log.json")
        performance = read_json_cache("performance.json")

        # === 인사이트 Pre-fetching [v4.1] ===
        all_insights = read_json("insights.json")
        current_env = market_env or {}

        env_matched = []
        env_unmatched = []

        for ins in all_insights:
            if ins.get("invalidated"):
                continue
            tc = ins.get("trigger_conditions", {})
            em = tc.get("env_match", {})
            if em and all(current_env.get(k) == v for k, v in em.items()):
                env_matched.append(ins)
            else:
                env_unmatched.append(ins)

        insights_text = "[env 매칭 인사이트 — 전체]\n"
        for ins in env_matched:
            insights_text += json.dumps(ins, ensure_ascii=False) + "\n"

        conf_order = {"high": 0, "medium": 1, "low": 2}
        env_unmatched.sort(key=lambda x: (conf_order.get(x.get("confidence", "low"), 3),
                                           x.get("last_used_at", "") or ""),
                           reverse=False)
        env_unmatched.sort(key=lambda x: (conf_order.get(x.get("confidence", "low"), 3),))
        top10 = env_unmatched[:10]

        insights_text += "\n[비매칭 인사이트 — 요약 상위 {}개]\n".format(len(top10))
        for ins in top10:
            warn_tag = " ⚠️" if ins.get("warnings") else ""
            insights_text += "{} | {} | {}({}) | wr:{} | {}{}\n".format(
                ins.get("id", ""), ins.get("category", ""), ins.get("confidence", ""),
                ins.get("sample_count", 0), ins.get("win_rate", 0),
                ins.get("content", "")[:80], warn_tag)

        if len(env_unmatched) > 10:
            insights_text += "→ 추가 {}개. get_insight_detail로 조회 가능.\n".format(len(env_unmatched) - 10)

        recent_trades = trades[-10:] if isinstance(trades, list) else []
        by_env = performance.get("by_environment", {}) if isinstance(performance, dict) else {}

        last_analysis_conclusion = "첫 분석"
        if isinstance(current_analysis, dict):
            last_analysis_conclusion = current_analysis.get("summary") or "첫 분석"

        return f"""[현재 상태]
현재가: {price} USDT
24H 변동: {change_24h}%
시장 환경: 추세={market_env.get('trend', 'unknown')}, 변동성={market_env.get('volatility', 'unknown')}, 펀딩={market_env.get('funding', 'neutral')}, 요일={market_env.get('day', 'weekday')}

[포지션]
{position_summary or '없음'}

[잔고]
{balance} USDT (Stage {TRADING_STAGE})

[활성 시나리오]
{_json_or_text(scenarios) if scenarios else '없음'}

[활성 알림]
{_json_or_text(alerts) if alerts else '없음'}

[활성 원칙]
{_json_or_text(principles) if principles else '아직 없음'}

[활성 인사이트]
{insights_text if insights_text else '아직 없음'}

[최근 10 거래 요약]
{_json_or_text(recent_trades) if recent_trades else '거래 이력 없음'}

[성과 통계]
오늘 손익: {performance.get('daily_pnl', 0)} USDT
주간 손익: {performance.get('weekly_pnl', 0)} USDT
전체: {performance.get('total_trades', 0)}회, 승률 {performance.get('win_rate', 0)}%, 평균 R:R {performance.get('avg_rr', 0)}, EV {performance.get('ev', 0)}
환경별: {_json_or_text(by_env)}

[이전 분석 결론]
{last_analysis_conclusion}

[트리거]
{trigger_type}: {trigger_description}"""

    def _build_messages(self, trigger_type: str, trigger_description: str, last_price: Optional[float]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        system_blocks = [
            {"type": "text", "text": PART_A_PROMPT, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": PART_B_PROMPT, "cache_control": {"type": "ephemeral"}},
        ]
        part_c = self._build_part_c(trigger_type, trigger_description, last_price)
        messages = [{"role": "user", "content": [{"type": "text", "text": part_c}]}]
        return system_blocks, messages

    def _get_anthropic_client(self) -> Any:
        anthropic_module = importlib.import_module("anthropic")
        return anthropic_module.Anthropic(api_key=CLAUDE_API_KEY)

    def run_trigger(self, trigger_type: str, trigger_description: str, last_price: Optional[float] = None) -> Dict[str, Any]:
        if not self._enforce_hourly_limit(trigger_type):
            return {"ok": False, "reason": "시간당 Claude 호출 제한 초과"}

        client = self._get_anthropic_client()
        system_blocks, messages = self._build_messages(trigger_type, trigger_description, last_price)
        max_iterations = MAX_ITERATIONS.get(trigger_type, 3)

        self._record_call()
        usage_logs: List[Dict[str, Any]] = []

        for iteration in range(max_iterations):
            response = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=4096,
                system=system_blocks,
                tools=TOOLS,
                messages=messages,
            )

            usage = getattr(response, "usage", None)
            usage_logs.append(
                {
                    "iteration": iteration + 1,
                    "input_tokens": getattr(usage, "input_tokens", 0) if usage else 0,
                    "output_tokens": getattr(usage, "output_tokens", 0) if usage else 0,
                    "at": _utc_now_iso(),
                }
            )
            logging.info("Claude usage iter=%s input=%s output=%s", iteration + 1, usage_logs[-1]["input_tokens"], usage_logs[-1]["output_tokens"])

            content_blocks = list(getattr(response, "content", []) or [])
            tool_uses = [b for b in content_blocks if getattr(b, "type", None) == "tool_use"]

            text_chunks = [getattr(b, "text", "") for b in content_blocks if getattr(b, "type", None) == "text"]
            assistant_content = []
            for b in content_blocks:
                if getattr(b, "type", None) == "text":
                    assistant_content.append({"type": "text", "text": b.text})
                elif getattr(b, "type", None) == "tool_use":
                    assistant_content.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
            if assistant_content:
                messages.append({"role": "assistant", "content": assistant_content})

            if not tool_uses:
                return {"ok": True, "response": "\n".join(text_chunks), "usage": usage_logs}

            tool_results = []
            conclude = False
            for tool in tool_uses:
                name = tool.name
                tool_input = tool.input if isinstance(tool.input, dict) else {}
                result = self._execute_tool(name, tool_input, last_price=last_price)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
                if name == "conclude_review":
                    conclude = True

            messages.append({"role": "user", "content": tool_results})

            if conclude:
                return {"ok": True, "response": "conclude_review", "usage": usage_logs}

        return {"ok": False, "reason": f"max_iterations({max_iterations}) 초과", "usage": usage_logs}

    def _execute_tool(self, name: str, tool_input: Dict[str, Any], last_price: Optional[float]) -> Dict[str, Any]:
        routes = {
            "get_candles": self._tool_get_candles,
            "save_analysis": self._tool_save_analysis,
            "save_scenario": self._tool_save_scenario,
            "set_alert": self._tool_set_alert,
            "remove_alert": self._tool_remove_alert,
            "open_position": self._tool_open_position,
            "close_position": self._tool_close_position,
            "modify_sl": self._tool_modify_sl,
            "modify_tp": self._tool_modify_tp,
            "save_trade_memo": self._tool_save_trade_memo,
            "save_insight": self._tool_save_insight,
            "save_principle": self._tool_save_principle,
            "update_analysis_routine": self._tool_update_analysis_routine,
            "conclude_review": self._tool_conclude_review,
            "send_telegram": self._tool_send_telegram,
            "get_insight_detail": self._tool_get_insight_detail,
        }
        handler = routes.get(name)
        if not handler:
            return {"ok": False, "reason": f"unknown tool: {name}"}
        return handler(tool_input, last_price=last_price)

    def _tool_get_candles(self, tool_input: Dict[str, Any], last_price: Optional[float]) -> Dict[str, Any]:
        timeframe = tool_input["timeframe"]
        count = int(tool_input["count"])
        raw = get_candles(SYMBOL, timeframe, count)
        rows = raw.get("data", [])
        normalized = []
        for r in rows:
            normalized.append(
                {
                    "ts": r[0],
                    "open": r[1],
                    "high": r[2],
                    "low": r[3],
                    "close": r[4],
                    "volume": r[5],
                }
            )
        indicators = calculate_indicators(normalized)
        return {"ok": True, "candles": normalized, "indicators": indicators}

    def _tool_save_analysis(self, tool_input: Dict[str, Any], last_price: Optional[float]) -> Dict[str, Any]:
        payload = dict(tool_input)
        payload["updated_at"] = _utc_now_iso()
        write_json_cache("current_analysis.json", payload)
        return {"ok": True}

    def _tool_save_scenario(self, tool_input: Dict[str, Any], last_price: Optional[float]) -> Dict[str, Any]:
        scenarios = read_json_cache("scenarios.json")
        scenarios.append({**tool_input, "created_at": _utc_now_iso(), "created_by": "CHLOE"})
        write_json_cache("scenarios.json", scenarios)
        return {"ok": True, "count": len(scenarios)}

    def _tool_set_alert(self, tool_input: Dict[str, Any], last_price: Optional[float]) -> Dict[str, Any]:
        alerts = read_json_cache("alerts.json")
        alert_id = f"alert_{len(alerts) + 1:03d}"
        alerts.append({"id": alert_id, **tool_input, "created_at": _utc_now_iso(), "created_by": "CHLOE"})
        write_json_cache("alerts.json", alerts)
        return {"ok": True, "alert_id": alert_id}

    def _tool_remove_alert(self, tool_input: Dict[str, Any], last_price: Optional[float]) -> Dict[str, Any]:
        alerts = read_json_cache("alerts.json")
        before = len(alerts)
        alerts = [a for a in alerts if a.get("id") != tool_input.get("alert_id")]
        write_json_cache("alerts.json", alerts)
        return {"ok": True, "removed": before - len(alerts)}

    def _tool_open_position(self, tool_input: Dict[str, Any], last_price: Optional[float]) -> Dict[str, Any]:
        return open_position(
            action=tool_input["action"],
            ord_type=tool_input.get("ordType", "market"),
            sl_price=float(tool_input["sl_price"]),
            decision_price=float(last_price or 0.0),
            last_price=float(last_price or 0.0),
            tp_price=tool_input.get("tp_price"),
            acceptable_price_range=tool_input.get("acceptable_price_range"),
            px=tool_input.get("px"),
            reason=tool_input.get("reason", ""),
            market_env=(read_json_cache("current_analysis.json") or {}).get("market_environment"),
            active_indicators=[i.get("name") for i in (read_json_cache("analysis_routine.json") or {}).get("indicators", []) if i.get("active")],
            override_principle_ids=tool_input.get("override_principle_ids"),
        )

    def _tool_close_position(self, tool_input: Dict[str, Any], last_price: Optional[float]) -> Dict[str, Any]:
        close_percent = float(tool_input.get("close_percent", 100)) / 100.0
        if TRADING_STAGE == 1:
            return close_paper_position(
                exit_price=float(last_price or 0.0),
                close_percent=close_percent,
                reason=tool_input.get("reason", ""),
            )
        return {"ok": False, "reason": "Stage 2~3 close_position은 scheduler/websocket 통합 후 구현"}

    def _tool_modify_sl(self, tool_input: Dict[str, Any], last_price: Optional[float]) -> Dict[str, Any]:
        if TRADING_STAGE == 1:
            return update_paper_sl(float(tool_input["new_sl_price"]))
        # Stage 2~3: OKX 포지션/algo order에서 현재 SL 정보 조회 필요
        # 현재는 executor.modify_sl()에 필요한 algo_id를 WebSocket/REST에서 가져와야 함
        return {"ok": False, "reason": "Stage 2~3 modify_sl은 scheduler/websocket 통합 후 구현"}

    def _tool_modify_tp(self, tool_input: Dict[str, Any], last_price: Optional[float]) -> Dict[str, Any]:
        if TRADING_STAGE == 1:
            tp = float(tool_input.get("new_tp_price", 0))
            return update_paper_tp(None if tp == 0 else tp)
        return {"ok": False, "reason": "Stage 2~3 modify_tp는 executor 통합 예정"}

    def _tool_save_trade_memo(self, tool_input: Dict[str, Any], last_price: Optional[float]) -> Dict[str, Any]:
        trades = read_json_cache("trade_log.json")
        updated = False
        for t in trades:
            if t.get("id") == tool_input.get("trade_id"):
                t["memo"] = tool_input.get("memo", "")
                updated = True
                break
        write_json_cache("trade_log.json", trades)
        return {"ok": True, "updated": updated}

    def _tool_save_insight(self, tool_input: Dict[str, Any], last_price: Optional[float]) -> Dict[str, Any]:
        action = tool_input.get("action", "create")
        if action == "create":
            insights = read_json("insights.json")
            insight_data = {
                "id": "ins_{:03d}".format(len(insights) + 1),
                "content": tool_input.get("content", ""),
                "category": tool_input.get("category", "general"),
                "supporting_data": tool_input.get("supporting_data", ""),
                "sample_count": len(tool_input.get("origin_trades", [])),
                "win_rate": 0,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "last_used_at": "",
                "warnings": [],
                "trigger_conditions": tool_input.get("trigger_conditions", {}),
                "reasoning_chain": {
                    "origin_trades": tool_input.get("origin_trades", []),
                    "key_observation": tool_input.get("key_observation", ""),
                    "failed_attempts": tool_input.get("failed_attempts", []),
                    "supersedes": tool_input.get("supersedes"),
                    "evolution_history": []
                }
            }
            validation = validate_insight(insight_data)
            insights.append(insight_data)
            write_json("insights.json", insights)
            return {"saved": insight_data["id"], **validation}

        if action in ("archive", "invalidate"):
            insight_id = tool_input.get("insight_id", "")
            reason = tool_input.get("reason", "")
            insights = read_json("insights.json")
            for ins in list(insights):
                if ins.get("id") == insight_id:
                    if action == "invalidate":
                        ins["invalidated"] = True
                        ins["invalidated_reason"] = reason
                        ins["invalidated_at"] = datetime.now(timezone.utc).isoformat()
                    elif action == "archive":
                        cold = read_json("cold_insights.json")
                        ins["archived_reason"] = reason
                        ins["archived_at"] = datetime.now(timezone.utc).isoformat()
                        cold.append(ins)
                        write_json("cold_insights.json", cold)
                        insights.remove(ins)
                    break
            write_json("insights.json", insights)
            return {"action": action, "insight_id": insight_id, "status": "done"}

        return {"error": "unsupported action"}

    def _tool_save_principle(self, tool_input: Dict[str, Any], last_price: Optional[float]) -> Dict[str, Any]:
        principles = read_json("principles.json")
        principle_data = {
            "id": "prin_{:03d}".format(len(principles) + 1),
            "content": tool_input.get("content", ""),
            "based_on_insight_id": tool_input.get("based_on_insight_id", ""),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "trigger_conditions": tool_input.get("trigger_conditions", {}),
            "alert_level": tool_input.get("alert_level", "block_suggest"),
            "trigger_history": {"total_triggered": 0, "followed": 0, "ignored": 0, "ignored_outcomes": []},
            "cooloff_remaining": 0
        }
        validation = validate_principle(principle_data)
        principles.append(principle_data)
        write_json("principles.json", principles)
        return {"saved": principle_data["id"], **validation}

    def _tool_update_analysis_routine(self, tool_input: Dict[str, Any], last_price: Optional[float]) -> Dict[str, Any]:
        routine = read_json_cache("analysis_routine.json")
        target = tool_input["indicator"]
        found = False
        for row in routine.get("indicators", []):
            if row.get("name") == target:
                row["active"] = bool(tool_input["active"])
                if "params" in tool_input and isinstance(tool_input["params"], dict):
                    row["params"] = tool_input["params"]
                found = True
                break
        if not found:
            routine.setdefault("indicators", []).append(
                {
                    "name": target,
                    "active": bool(tool_input["active"]),
                    "params": tool_input.get("params", {}),
                }
            )
        routine["last_updated_at"] = _utc_now_iso()
        routine["last_updated_by"] = "CHLOE"
        write_json_cache("analysis_routine.json", routine)
        return {"ok": True}

    def _tool_get_insight_detail(self, tool_input: Dict[str, Any], last_price: Optional[float]) -> Dict[str, Any]:
        insight_id = tool_input.get("insight_id", "")
        cached = _get_cached_insight(insight_id)
        if cached:
            return cached

        insights = read_json("insights.json")
        detail = next((i for i in insights if i.get("id") == insight_id), None)
        if detail:
            tool_result = {
                "id": detail.get("id"),
                "content": detail.get("content"),
                "reasoning_chain": detail.get("reasoning_chain", {}),
                "trigger_conditions": detail.get("trigger_conditions", {}),
                "warnings": detail.get("warnings", []),
                "confidence": detail.get("confidence"),
                "sample_count": detail.get("sample_count"),
                "win_rate": detail.get("win_rate")
            }
            _cache_insight(insight_id, tool_result)
            return tool_result
        return {"error": "insight_id {} not found".format(insight_id)}

    def _tool_conclude_review(self, tool_input: Dict[str, Any], last_price: Optional[float]) -> Dict[str, Any]:
        return {"ok": True, "summary": tool_input.get("summary", "")}

    def _tool_send_telegram(self, tool_input: Dict[str, Any], last_price: Optional[float]) -> Dict[str, Any]:
        message = str(tool_input.get("message", ""))
        logging.info("[send_telegram stub] %s", message)
        return {"ok": True, "logged": True}
