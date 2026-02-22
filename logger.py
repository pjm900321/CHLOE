from __future__ import annotations

import copy
import json
import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import numpy as np

import config
from indicators import DEFAULT_ANALYSIS_ROUTINE


CACHE_DIR = Path("cache")
LOG_DIR = Path("logs")

CACHE_DEFAULTS: Dict[str, Any] = {
    "current_analysis.json": {},
    "scenarios.json": [],
    "paper_position.json": {"has_position": False},
    "trade_log.json": [],
    "insights.json": [],
    "cold_insights.json": [],
    "principles.json": [],
    "performance.json": {"total_trades": 0, "last_rebuild": "", "learning_health": {}},
    "analysis_routine.json": DEFAULT_ANALYSIS_ROUTINE,
    "alerts.json": [],
    "daily_loss.json": {"daily": 0, "weekly": 0, "daily_reset": "", "weekly_reset": ""},
    "system_state.json": {"shutdown": "unknown"},
}

SHEETS_MAPPING = {
    "CHLOE_차트분석": "current_analysis.json",
    "CHLOE_시나리오": "scenarios.json",
    "CHLOE_인사이트": "insights.json",
    "CHLOE_원칙": "principles.json",
    "CHLOE_매매기록": "trade_log.json",
    "CHLOE_성과": "performance.json",
}


class JsonCacheError(Exception):
    pass


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _default_for(filename: str) -> Any:
    if filename not in CACHE_DEFAULTS:
        raise JsonCacheError(f"Unsupported cache file: {filename}")
    return copy.deepcopy(CACHE_DEFAULTS[filename])


def _path(filename: str) -> Path:
    return CACHE_DIR / filename


def _backup_path(filename: str) -> Path:
    return CACHE_DIR / f"{filename}.bak"


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logfile = LOG_DIR / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.log"

    root = logging.getLogger()
    if root.handlers:
        return

    root.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    fh = logging.FileHandler(logfile, encoding="utf-8")
    fh.setFormatter(formatter)
    root.addHandler(fh)

    sh = logging.StreamHandler()
    sh.setFormatter(formatter)
    root.addHandler(sh)


def ensure_cache_files() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for filename in CACHE_DEFAULTS:
        fpath = _path(filename)
        if not fpath.exists():
            write_json_cache(filename, _default_for(filename), create_backup=False)
        else:
            read_json_cache(filename)


def read_json_cache(filename: str) -> Any:
    fpath = _path(filename)
    if not fpath.exists():
        default = _default_for(filename)
        write_json_cache(filename, default, create_backup=False)
        return default

    try:
        return json.loads(fpath.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logging.warning("JSON parse failed: %s, trying backup", filename)
        return _recover_from_backup_or_default(filename)


def _recover_from_backup_or_default(filename: str) -> Any:
    bpath = _backup_path(filename)
    if bpath.exists():
        try:
            data = json.loads(bpath.read_text(encoding="utf-8"))
            _path(filename).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            logging.warning("Recovered %s from .bak", filename)
            return data
        except json.JSONDecodeError:
            logging.error("Backup JSON parse failed: %s", bpath.name)

    default = _default_for(filename)
    _path(filename).write_text(json.dumps(default, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.warning("Recreated %s from default", filename)
    return default


def write_json_cache(filename: str, data: Any, create_backup: bool = True) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fpath = _path(filename)
    bpath = _backup_path(filename)

    if create_backup and fpath.exists():
        shutil.copy2(fpath, bpath)

    fpath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def append_trade_log(record: Dict[str, Any]) -> None:
    trades = read_json_cache("trade_log.json")
    trades.append(record)
    write_json_cache("trade_log.json", trades)


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _calc_mdd(equity_curve: List[float]) -> float:
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for eq in equity_curve:
        peak = max(peak, eq)
        dd = (peak - eq) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
    return max_dd


def _build_environment_stats(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for t in trades:
        env = t.get("env", {}) or {}
        key = f"{env.get('trend', 'unknown')}_{env.get('volatility', 'unknown')}"
        grouped.setdefault(key, []).append(t)

    out: Dict[str, Any] = {}
    for key, rows in grouped.items():
        pnls = [_safe_float(r.get("pnl_usdt")) for r in rows]
        rrs = [_safe_float(r.get("pnl_r")) for r in rows]
        wins = sum(1 for p in pnls if p > 0)
        out[key] = {
            "trades": len(rows),
            "win_rate": wins / len(rows) if rows else 0.0,
            "avg_rr": float(np.mean(rrs)) if rrs else 0.0,
        }
    return out


def _build_indicator_stats(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    # trade_log에 active_indicators(list[str])가 있으면 집계, 없으면 빈 dict
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for t in trades:
        tags = t.get("active_indicators", []) or []
        for tag in tags:
            grouped.setdefault(str(tag), []).append(t)

    out: Dict[str, Any] = {}
    for key, rows in grouped.items():
        pnls = [_safe_float(r.get("pnl_usdt")) for r in rows]
        rrs = [_safe_float(r.get("pnl_r")) for r in rows]
        wins = sum(1 for p in pnls if p > 0)
        out[key] = {
            "trades": len(rows),
            "win_rate": wins / len(rows) if rows else 0.0,
            "avg_rr": float(np.mean(rrs)) if rrs else 0.0,
        }
    return out


def _environment_correlations(trades: List[Dict[str, Any]]) -> Dict[str, float]:
    if len(trades) < 3:
        return {}

    trend_map = {"down": -1, "sideways": 0, "up": 1}
    vol_map = {"low": -1, "normal": 0, "high": 1}
    funding_map = {"short_bias": -1, "neutral": 0, "long_bias": 1}
    day_map = {"weekend": 0, "weekday": 1}

    pnl = np.array([_safe_float(t.get("pnl_usdt")) for t in trades], dtype=float)
    env_trend = np.array([trend_map.get((t.get("env", {}) or {}).get("trend"), 0) for t in trades], dtype=float)
    env_vol = np.array([vol_map.get((t.get("env", {}) or {}).get("volatility"), 0) for t in trades], dtype=float)
    env_funding = np.array([funding_map.get((t.get("env", {}) or {}).get("funding"), 0) for t in trades], dtype=float)
    env_day = np.array([day_map.get((t.get("env", {}) or {}).get("day"), 0) for t in trades], dtype=float)

    def corr(a: np.ndarray, b: np.ndarray) -> float:
        if np.std(a) == 0 or np.std(b) == 0:
            return 0.0
        return float(np.corrcoef(a, b)[0, 1])

    return {
        "trend_vs_pnl": corr(env_trend, pnl),
        "volatility_vs_pnl": corr(env_vol, pnl),
        "funding_vs_pnl": corr(env_funding, pnl),
        "day_vs_pnl": corr(env_day, pnl),
    }


def _parse_iso_utc(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _rebuild_daily_weekly_pnl(trades: List[Dict[str, Any]]) -> Dict[str, float]:
    now = datetime.now(timezone.utc)
    start_of_day = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    start_of_week = start_of_day - timedelta(days=start_of_day.weekday())

    daily = 0.0
    weekly = 0.0
    for t in trades:
        dt = _parse_iso_utc(str(t.get("exit_time", "")))
        if dt is None:
            continue
        pnl = _safe_float(t.get("pnl_usdt", 0.0))
        if dt >= start_of_day:
            daily += pnl
        if dt >= start_of_week:
            weekly += pnl
    return {"daily": daily, "weekly": weekly}


def daily_stats_rebuild() -> Dict[str, Any]:
    trades = read_json_cache("trade_log.json")
    daily_loss = read_json_cache("daily_loss.json")

    pnls = [_safe_float(t.get("pnl_usdt")) for t in trades]
    rs = [_safe_float(t.get("pnl_r")) for t in trades]

    total_trades = len(trades)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    win_rate = (len(wins) / total_trades) if total_trades else 0.0
    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss_abs = abs(float(np.mean(losses))) if losses else 0.0
    avg_rr = float(np.mean(rs)) if rs else 0.0
    ev = (win_rate * avg_win) - ((1 - win_rate) * avg_loss_abs)

    equity = []
    running = 0.0
    for p in pnls:
        running += p
        equity.append(running)
    max_drawdown = _calc_mdd(equity)

    current_win = current_loss = max_win = max_loss = 0
    for p in pnls:
        if p > 0:
            current_win += 1
            current_loss = 0
        else:
            current_loss += 1
            current_win = 0
        max_win = max(max_win, current_win)
        max_loss = max(max_loss, current_loss)

    performance = {
        "total_trades": total_trades,
        "win_rate": win_rate,
        "avg_rr": avg_rr,
        "ev": ev,
        "max_drawdown": max_drawdown,
        "streak": {
            "current_win": current_win,
            "current_loss": current_loss,
            "max_win": max_win,
            "max_loss": max_loss,
        },
        "by_environment": _build_environment_stats(trades),
        "by_indicator": _build_indicator_stats(trades),
        "environment_correlations": _environment_correlations(trades),
        "daily_pnl": 0.0,
        "weekly_pnl": 0.0,
        "last_rebuild": _utc_now_iso(),
    }

    rebuilt_pnl = _rebuild_daily_weekly_pnl(trades)
    performance["daily_pnl"] = rebuilt_pnl["daily"]
    performance["weekly_pnl"] = rebuilt_pnl["weekly"]

    daily_loss["daily"] = rebuilt_pnl["daily"]
    daily_loss["weekly"] = rebuilt_pnl["weekly"]
    if not daily_loss.get("daily_reset"):
        daily_loss["daily_reset"] = _utc_now_iso()
    if not daily_loss.get("weekly_reset"):
        daily_loss["weekly_reset"] = _utc_now_iso()

    write_json_cache("daily_loss.json", daily_loss)
    write_json_cache("performance.json", performance)
    logging.info("daily_stats_rebuild completed: trades=%s win_rate=%.3f", total_trades, win_rate)
    return performance


def _cell_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return "" if value is None else str(value)


def _rows_for_payload(payload: Any) -> List[List[str]]:
    if isinstance(payload, list):
        if not payload:
            return [["empty"], ["true"]]
        if all(isinstance(item, dict) for item in payload):
            headers = sorted({k for item in payload for k in item.keys()})
            rows = [headers]
            for item in payload:
                rows.append([_cell_value(item.get(h)) for h in headers])
            return rows
        rows=[["value"]]
        rows.extend([_cell_value(item)] for item in payload)
        return rows

    if isinstance(payload, dict):
        return [["key", "value"]] + [[str(k), _cell_value(v)] for k, v in payload.items()]

    return [["value"], [_cell_value(payload)]]


def sync_to_sheets() -> bool:
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        from config_secret import GOOGLE_SHEETS_CREDS_FILE, GOOGLE_SHEETS_SPREADSHEET_ID

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_file(GOOGLE_SHEETS_CREDS_FILE, scopes=scopes)
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(GOOGLE_SHEETS_SPREADSHEET_ID)

        for tab, filename in SHEETS_MAPPING.items():
            ws = sh.worksheet(tab)
            payload = read_json_cache(filename)
            rows = _rows_for_payload(payload)
            ws.clear()
            ws.update("A1", rows)

        logging.info("Google Sheets sync success")
        return True
    except Exception as exc:
        # 요구사항: 실패 시 로컬 JSON 유지, 텔레그램 알림 없이 로그만
        logging.warning("Google Sheets sync failed: %s", exc)
        return False


def read_json(filename: str) -> Any:
    return read_json_cache(filename)


def write_json(filename: str, data: Any) -> None:
    write_json_cache(filename, data)


def validate_insight(insight_data: dict) -> dict:
    """
    인사이트 품질 검사 5항목. 반환: {"status": "saved"|"saved_with_warnings", "warnings": [...]}
    """
    warnings = []
    matched_trades = []

    # 1. 샘플 충분성
    origin_trades = insight_data.get("origin_trades", [])
    if len(origin_trades) < config.MIN_TRADES_FOR_INSIGHT:
        warnings.append("insufficient_sample: origin_trades < {}".format(config.MIN_TRADES_FOR_INSIGHT))
        # confidence 강제 low
        insight_data["confidence"] = "low"

    # 2. 환경 편향 — origin_trades의 env를 trade_log.json에서 조회
    trade_log = read_json("trade_log.json")
    if origin_trades and trade_log:
        matched_trades = [t for t in trade_log if t.get("id") in origin_trades]
        if matched_trades:
            envs = [str(t.get("env", {}).get("trend", "")) + "_" + str(t.get("env", {}).get("volatility", "")) for t in matched_trades]
            from collections import Counter
            most_common_count = Counter(envs).most_common(1)[0][1] if envs else 0
            if len(envs) > 0 and most_common_count / len(envs) > config.ENV_BIAS_THRESHOLD:
                warnings.append("environment_bias: {}% same env".format(int(most_common_count / len(envs) * 100)))

    # 3. 시간 편향 — 모든 origin_trades가 최근 N일 이내
    if matched_trades:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=config.TIME_BIAS_DAYS)
        all_recent = all(
            datetime.fromisoformat(t.get("entry_time", "2000-01-01T00:00:00Z").replace("Z", "+00:00")) > cutoff
            for t in matched_trades
        )
        if all_recent and len(matched_trades) > 1:
            warnings.append("time_bias: all trades within last {} days".format(config.TIME_BIAS_DAYS))

    # 4. 중복/모순 — trigger_conditions 구조적 매칭
    trigger_cond = insight_data.get("trigger_conditions", {})
    if trigger_cond:
        existing_insights = read_json("insights.json")
        for existing in existing_insights:
            if existing.get("id") == insight_data.get("id"):
                continue
            existing_tc = existing.get("trigger_conditions", {})
            if not existing_tc:
                continue
            # env_match + action_match 비교
            env_match = trigger_cond.get("env_match", {}) == existing_tc.get("env_match", {})
            action_match = trigger_cond.get("action_match", []) == existing_tc.get("action_match", [])
            if env_match and action_match:
                warnings.append("duplicate_candidate: similar to {}".format(existing.get("id", "unknown")))
                break

    # 5. Confidence 자동 조정 (sample_count 기반)
    sample = insight_data.get("sample_count", len(origin_trades))
    if sample < 5:
        insight_data["confidence"] = "low"
    elif sample < 10:
        insight_data["confidence"] = "low"
    elif sample < 20:
        insight_data["confidence"] = "medium"
    else:
        insight_data["confidence"] = "high"

    insight_data["warnings"] = warnings
    status = "saved_with_warnings" if warnings else "saved"
    return {"status": status, "warnings": warnings}


def validate_principle(principle_data: dict) -> dict:
    """원칙 승격 품질 검사."""
    warnings = []
    based_on = principle_data.get("based_on_insight_id", "")
    if based_on:
        insights = read_json("insights.json")
        source = next((i for i in insights if i.get("id") == based_on), None)
        if source:
            if source.get("sample_count", 0) < config.PRINCIPLE_MIN_SAMPLE:
                warnings.append("low_sample: source insight has {} trades (min {})".format(
                    source.get("sample_count", 0), config.PRINCIPLE_MIN_SAMPLE))
            if source.get("win_rate", 0) < config.PRINCIPLE_MIN_WINRATE:
                warnings.append("low_winrate: source insight win_rate {:.0%} (min {:.0%})".format(
                    source.get("win_rate", 0), config.PRINCIPLE_MIN_WINRATE))
    status = "saved_with_warnings" if warnings else "saved"
    return {"status": status, "warnings": warnings}


def build_learning_health() -> dict:
    """학습 건강 보고서 생성. performance.json["learning_health"]에 저장."""
    insights = read_json("insights.json")
    principles = read_json("principles.json")
    trade_log = read_json("trade_log.json")
    performance = read_json("performance.json")

    now = datetime.now(timezone.utc)

    # insight_usage
    insight_usage = {}
    for ins in insights:
        ins_id = ins.get("id", "")
        last_used = ins.get("last_used_at", "")
        last_7d = False
        if last_used:
            try:
                lu = datetime.fromisoformat(last_used.replace("Z", "+00:00"))
                last_7d = (now - lu).days <= 7
            except Exception:
                pass
        insight_usage[ins_id] = {"referenced": ins.get("sample_count", 0), "last_7d": last_7d}

    # principle_compliance
    principle_compliance = {}
    for p in principles:
        pid = p.get("id", "")
        th = p.get("trigger_history", {})
        principle_compliance[pid] = {
            "triggered": th.get("total_triggered", 0),
            "followed": th.get("followed", 0),
            "ignored": th.get("ignored", 0)
        }

    # insight_accuracy_drift — 전체 win_rate vs 최근 5회
    insight_accuracy_drift = {}
    for ins in insights:
        ins_id = ins.get("id", "")
        overall_wr = ins.get("win_rate", 0)
        origin = ins.get("reasoning_chain", {}).get("origin_trades", [])
        if len(origin) >= 5:
            recent_5 = origin[-5:]
            wins = sum(1 for tid in recent_5
                      for t in trade_log if t.get("id") == tid and t.get("pnl_usdt", 0) > 0)
            recent_wr = wins / 5
            drift = recent_wr - overall_wr
            insight_accuracy_drift[ins_id] = {
                "overall_wr": overall_wr, "recent_5_wr": recent_wr, "drift": round(drift, 3)
            }

    # category_balance
    cat_count = {"entry": 0, "exit": 0, "risk": 0, "market": 0, "general": 0}
    for ins in insights:
        cat = ins.get("category", "general")
        if cat in cat_count:
            cat_count[cat] += 1

    # overfit_risk
    total = len(insights) if insights else 1
    low_sample = sum(1 for i in insights if i.get("sample_count", 0) < config.MIN_TRADES_FOR_INSIGHT)
    env_biased = sum(1 for i in insights if "environment_bias" in str(i.get("warnings", [])))

    # regime_shift_warning
    regime_shift = False
    drift_negatives = [v for v in insight_accuracy_drift.values() if v.get("drift", 0) <= -0.20]
    if len(drift_negatives) >= 3:
        regime_shift = True

    health = {
        "insight_usage": insight_usage,
        "principle_compliance": principle_compliance,
        "insight_accuracy_drift": insight_accuracy_drift,
        "category_balance": cat_count,
        "overfit_risk": {
            "low_sample_ratio": round(low_sample / total, 2),
            "env_bias_ratio": round(env_biased / total, 2)
        },
        "regime_shift_warning": regime_shift,
        "generated_at": now.isoformat()
    }

    performance["learning_health"] = health
    write_json("performance.json", performance)
    return health
