from __future__ import annotations

import importlib
import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from ai_brain import AIBrain
from config import CLAUDE_MODEL, LEVERAGE, SYMBOL
from config_secret import CLAUDE_API_KEY
from data import get_instruments, get_positions, set_leverage
from logger import ensure_cache_files, read_json_cache, setup_logging, write_json_cache
from scheduler import Scheduler
from telegram_handler import get_handler, send_log_file, send_message
from websocket_client import OKXWebSocketClient
import executor


RUNTIME_CONTRACT: Dict[str, float] = {
    "ctVal": 0.01,
    "lotSz": 1.0,
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _test_okx() -> None:
    get_instruments(inst_type="SWAP", inst_id=SYMBOL)


def _test_claude() -> None:
    anthropic_module = importlib.import_module("anthropic")
    client = anthropic_module.Anthropic(api_key=CLAUDE_API_KEY)
    client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1,
        messages=[{"role": "user", "content": "ping"}],
    )


def _test_sheets() -> None:
    import gspread
    from google.oauth2.service_account import Credentials
    from config_secret import GOOGLE_SHEETS_CREDS_FILE, GOOGLE_SHEETS_SPREADSHEET_ID

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(GOOGLE_SHEETS_CREDS_FILE, scopes=scopes)
    gc = gspread.authorize(creds)
    gc.open_by_key(GOOGLE_SHEETS_SPREADSHEET_ID)


def _retry_api_tests(max_retries: int = 3) -> bool:
    tests = [
        ("OKX REST", _test_okx),
        ("Claude API", _test_claude),
        ("Google Sheets", _test_sheets),
    ]

    for attempt in range(1, max_retries + 1):
        all_ok = True
        for name, fn in tests:
            try:
                fn()
            except Exception as exc:
                all_ok = False
                send_message(f"{name} 연결 테스트 실패 ({attempt}/{max_retries}): {exc}", is_error=True, is_critical=True)
                logging.error("%s test failed (%s/%s): %s", name, attempt, max_retries, exc)
        if all_ok:
            return True
        time.sleep(2)

    return False


def _load_contract_spec() -> None:
    global RUNTIME_CONTRACT
    resp = get_instruments(inst_type="SWAP", inst_id=SYMBOL)
    ct_val = float(resp.get("ctVal") or 0.01)
    lot_sz = float(resp.get("lotSz") or 1)
    RUNTIME_CONTRACT = {"ctVal": ct_val, "lotSz": lot_sz}

    executor.CT_VAL = RUNTIME_CONTRACT["ctVal"]
    executor.LOT_SZ = RUNTIME_CONTRACT["lotSz"]

    if abs(ct_val - 0.01) > 1e-12 or abs(lot_sz - 1.0) > 1e-12:
        logging.warning("Contract spec differs from default: ctVal=%s lotSz=%s", ct_val, lot_sz)


def _handle_abnormal_recovery(state: Dict[str, Any], ai_brain: AIBrain) -> None:
    send_message("비정상 종료 감지. OKX 이력 확인 중...", is_error=True, is_critical=True)

    try:
        positions = get_positions(SYMBOL)
        ai_brain.run_trigger_async(
            "user_message",
            f"부재 중 발생한 이벤트 점검 필요. 마지막 상태={state.get('shutdown')} / 현재 포지션={json.dumps(positions.get('data', []), ensure_ascii=False)}",
        )
    except Exception as exc:
        send_message(f"비정상 종료 복구 점검 실패: {exc}", is_error=True, is_critical=True)


def _start_telegram_polling(handler) -> threading.Thread:
    thread = threading.Thread(target=handler.poll_loop, daemon=True)
    thread.start()
    return thread


def _wait_first_price_and_trigger(ai_brain: AIBrain, scheduler: Scheduler, handler) -> None:
    while scheduler.last_price <= 0:
        if handler.shutdown_requested:
            return
        time.sleep(0.5)
    ai_brain.run_trigger_async("first_analysis", f"첫 가격 피드 수신: {scheduler.last_price}", last_price=scheduler.last_price)


def _has_open_position() -> bool:
    paper = read_json_cache("paper_position.json")
    if isinstance(paper, dict) and paper.get("has_position"):
        return True

    try:
        positions = get_positions(SYMBOL)
        for row in positions.get("data", []):
            if abs(float(row.get("pos", 0.0))) > 0:
                return True
    except Exception:
        pass
    return False


def _graceful_shutdown(ws_client: Optional[OKXWebSocketClient]) -> None:
    if _has_open_position():
        send_message(
            "SL/TP가 OKX에 등록돼 있으나 CHLOE가 관리 불가. 트레일링 스톱/부분 청산 불가.",
            is_error=True,
            is_critical=True,
        )

    state = read_json_cache("system_state.json")
    if not isinstance(state, dict):
        state = {}
    state.update({"shutdown": "normal", "time": _utc_now_iso(), "paused": False})
    write_json_cache("system_state.json", state)

    if ws_client is not None:
        ws_client.stop()


def main() -> None:
    ws_client: Optional[OKXWebSocketClient] = None
    setup_logging()

    try:
        # 1) cache 무결성 확인
        ensure_cache_files()

        # 2) system_state 확인
        state = read_json_cache("system_state.json")
        if not isinstance(state, dict):
            state = {"shutdown": "unknown"}

        abnormal_shutdown = state.get("shutdown") != "normal"

        ai_brain = AIBrain()
        telegram_handler = get_handler()

        if abnormal_shutdown:
            send_message("비정상 종료 감지. OKX 이력 확인 중...", is_error=True, is_critical=True)

        # 3) API 연결 테스트
        if not _retry_api_tests(max_retries=3):
            send_message("API 연결 테스트 3회 실패. 시스템 종료.", is_error=True, is_critical=True)
            return

        # 4) 계약 사양 조회
        _load_contract_spec()

        # 5) 레버리지 설정
        lev_resp = set_leverage(inst_id=SYMBOL, lever=LEVERAGE, mgn_mode="cross")
        if lev_resp.get("code") not in ("0", 0, None):
            send_message(f"레버리지 설정 응답 경고: {lev_resp}", is_error=True)

        # 6) 비정상 종료였다면 부재 중 이벤트 보고
        if abnormal_shutdown:
            _handle_abnormal_recovery(state, ai_brain)

        # 7) WebSocket 시작 + 콜백 등록
        scheduler = Scheduler(ai_brain=ai_brain, telegram_handler=telegram_handler)
        ws_client = OKXWebSocketClient(
            on_price_update=scheduler.on_price_update,
            on_order_update=scheduler.on_order_update,
            on_position_update=scheduler.on_position_update,
        )
        ws_client.start()

        # 8) 텔레그램 폴링 시작
        _start_telegram_polling(telegram_handler)

        # 9) 첫 가격 피드 수신 후 first_analysis
        _wait_first_price_and_trigger(ai_brain, scheduler, telegram_handler)

        # 메인 루프
        scheduler.run()

        # graceful shutdown
        _graceful_shutdown(ws_client)

    except Exception as exc:
        logging.exception("main crash: %s", exc)

        send_message(f"시스템 크래시: {exc}", is_error=True, is_critical=True)

        log_path = f"logs/{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.log"
        send_log_file(log_path)

        crash_state = {
            "shutdown": "crash",
            "error": str(exc),
            "crash_time": _utc_now_iso(),
        }
        write_json_cache("system_state.json", crash_state)

        if ws_client is not None:
            ws_client.stop()


if __name__ == "__main__":
    main()
