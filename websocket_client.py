from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import threading
import time
from typing import Any, Callable, Dict, Optional

from websocket import WebSocketApp

import config
from config import SYMBOL, WS_PRIVATE, WS_PRIVATE_DEMO, WS_PUBLIC, WS_PUBLIC_DEMO
from config_secret import OKX_API_KEY, OKX_PASSPHRASE, OKX_SECRET_KEY
from data import get_positions, get_ticker
from telegram_handler import send_message


class OKXWebSocketClient:
    def __init__(
        self,
        on_price_update: Optional[Callable[[float], None]] = None,
        on_order_update: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_position_update: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        self.on_price_update = on_price_update or (lambda _price: None)
        self.on_order_update = on_order_update or (lambda _data: None)
        self.on_position_update = on_position_update or (lambda _data: None)

        self.public_url, self.private_url = self._resolve_urls()
        self.public_ws: Optional[WebSocketApp] = None
        self.private_ws: Optional[WebSocketApp] = None

        self._running = False
        self._reconnecting = False
        self._fallback_running = False
        self._internal_positions: Dict[str, Any] = {}

    def _resolve_urls(self) -> tuple[str, str]:
        if config.TRADING_STAGE == 2:
            return WS_PUBLIC_DEMO, WS_PRIVATE_DEMO
        return WS_PUBLIC, WS_PRIVATE

    def _ws_login_payload(self) -> Dict[str, Any]:
        ts = str(int(time.time()))
        prehash = f"{ts}GET/users/self/verify"
        digest = hmac.new(OKX_SECRET_KEY.encode(), prehash.encode(), hashlib.sha256).digest()
        sign = base64.b64encode(digest).decode()
        return {
            "op": "login",
            "args": [
                {
                    "apiKey": OKX_API_KEY,
                    "passphrase": OKX_PASSPHRASE,
                    "timestamp": ts,
                    "sign": sign,
                }
            ],
        }

    def start(self) -> None:
        self._running = True
        self._connect_public()
        self._connect_private()

    def stop(self) -> None:
        self._running = False
        if self.public_ws:
            self.public_ws.close()
        if self.private_ws:
            self.private_ws.close()

    def _connect_public(self) -> None:
        def on_open(ws: WebSocketApp) -> None:
            sub = {"op": "subscribe", "args": [{"channel": "tickers", "instId": SYMBOL}]}
            ws.send(json.dumps(sub))
            logging.info("public ws subscribed: tickers")

        def on_message(_ws: WebSocketApp, msg: str) -> None:
            self._handle_public_message(msg)

        def on_error(_ws: WebSocketApp, err: Any) -> None:
            logging.error("public ws error: %s", err)

        def on_close(_ws: WebSocketApp, _code: Any, _reason: Any) -> None:
            logging.warning("public ws closed")
            if self._running and not self._reconnecting:
                self._reconnect(kind="public")

        self.public_ws = WebSocketApp(
            self.public_url,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )
        threading.Thread(target=self.public_ws.run_forever, daemon=True).start()

    def _connect_private(self) -> None:
        def on_open(ws: WebSocketApp) -> None:
            ws.send(json.dumps(self._ws_login_payload()))
            logging.info("private ws login sent")

        def on_message(ws: WebSocketApp, msg: str) -> None:
            data = json.loads(msg)
            if data.get("event") == "login" and data.get("code") == "0":
                sub = {
                    "op": "subscribe",
                    "args": [
                        {"channel": "orders", "instType": "SWAP"},
                        {"channel": "positions", "instType": "SWAP"},
                    ],
                }
                ws.send(json.dumps(sub))
                logging.info("private ws subscribed: orders, positions")
                return
            self._handle_private_message(msg)

        def on_error(_ws: WebSocketApp, err: Any) -> None:
            logging.error("private ws error: %s", err)

        def on_close(_ws: WebSocketApp, _code: Any, _reason: Any) -> None:
            logging.warning("private ws closed")
            if self._running and not self._reconnecting:
                self._reconnect(kind="private")

        self.private_ws = WebSocketApp(
            self.private_url,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )
        threading.Thread(target=self.private_ws.run_forever, daemon=True).start()

    def _handle_public_message(self, msg: str) -> None:
        try:
            data = json.loads(msg)
            if data.get("arg", {}).get("channel") == "tickers" and data.get("data"):
                price = float(data["data"][0].get("last", 0.0))
                if price > 0:
                    self.on_price_update(price)
        except Exception as exc:
            logging.error("public ws parse error: %s", exc)

    def _handle_private_message(self, msg: str) -> None:
        try:
            data = json.loads(msg)
            channel = data.get("arg", {}).get("channel")
            if channel == "orders":
                self.on_order_update(data)
            elif channel == "positions":
                if data.get("data"):
                    self._internal_positions = {"data": data.get("data")}
                self.on_position_update(data)
        except Exception as exc:
            logging.error("private ws parse error: %s", exc)

    def _reconnect(self, kind: str) -> None:
        if self._reconnecting:
            return
        self._reconnecting = True
        try:
            backoffs = [2, 4, 8, 16, 32]
            for wait in backoffs:
                if not self._running:
                    return
                logging.warning("%s ws reconnect attempt in %ss", kind, wait)
                time.sleep(wait)
                try:
                    if kind == "public":
                        self._connect_public()
                    else:
                        self._connect_private()
                    self._reconcile_positions_with_rest()
                    return
                except Exception as exc:
                    logging.error("%s ws reconnect failed: %s", kind, exc)
            send_message(f"{kind} WebSocket 재연결 5회 실패. REST 폴링 폴백 전환", is_error=True, is_critical=True)
            self._start_rest_polling_fallback()
        finally:
            self._reconnecting = False

    def _reconcile_positions_with_rest(self) -> None:
        rest_positions = get_positions(SYMBOL)
        ws_positions = self._internal_positions.get("data", [])
        rest_data = rest_positions.get("data", [])

        if json.dumps(ws_positions, sort_keys=True) != json.dumps(rest_data, sort_keys=True):
            self._internal_positions = {"data": rest_data}
            self.on_position_update({"arg": {"channel": "positions"}, "data": rest_data, "source": "rest_truth"})
            logging.warning("Position mismatch detected; REST truth applied")
            send_message("포지션 불일치 감지: REST 데이터를 기준으로 강제 동기화", is_error=True, is_critical=True)

    def _start_rest_polling_fallback(self) -> None:
        if self._fallback_running:
            return
        self._fallback_running = True

        def loop() -> None:
            while self._running:
                try:
                    ticker = get_ticker(SYMBOL)
                    tdata = ticker.get("data", [{}])[0] if ticker.get("data") else {}
                    price = float(tdata.get("last", 0.0))
                    if price > 0:
                        self.on_price_update(price)

                    rest_positions = get_positions(SYMBOL)
                    self.on_position_update(
                        {
                            "arg": {"channel": "positions"},
                            "data": rest_positions.get("data", []),
                            "source": "rest_poll",
                        }
                    )
                except Exception as exc:
                    logging.error("REST polling fallback error: %s", exc)
                time.sleep(30)

            self._fallback_running = False

        threading.Thread(target=loop, daemon=True).start()
