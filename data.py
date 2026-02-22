import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests

import config
from config import REST_BASE
from config_secret import OKX_API_KEY, OKX_PASSPHRASE, OKX_SECRET_KEY


class RetryLimitError(Exception):
    pass


def _iso_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _sign(timestamp: str, method: str, request_path: str, body: str) -> str:
    payload = f"{timestamp}{method.upper()}{request_path}{body}"
    digest = hmac.new(OKX_SECRET_KEY.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def _headers(method: str, request_path: str, body: str) -> Dict[str, str]:
    ts = _iso_timestamp()
    headers = {
        "OK-ACCESS-KEY": OKX_API_KEY,
        "OK-ACCESS-SIGN": _sign(ts, method, request_path, body),
        "OK-ACCESS-TIMESTAMP": ts,
        "OK-ACCESS-PASSPHRASE": OKX_PASSPHRASE,
        "Content-Type": "application/json",
    }
    if config.TRADING_STAGE == 2:
        headers["x-simulated-trading"] = "1"
    return headers


def _request(method: str, request_path: str, params: Optional[Dict[str, Any]] = None, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    url = f"{REST_BASE}{request_path}"
    payload = json.dumps(body, separators=(",", ":")) if body is not None else ""

    max_retries = 3
    backoffs = [2, 4, 8]

    for attempt in range(1 + max_retries):  # 0=첫 시도, 1~3=재시도
        try:
            response = requests.request(
                method=method.upper(),
                url=url,
                params=params,
                data=payload if body is not None else None,
                headers=_headers(method, request_path, payload),
                timeout=10,
            )
        except requests.RequestException:
            if attempt < max_retries:
                time.sleep(backoffs[attempt])
                continue
            raise RetryLimitError("Request failed after 3 retries")

        if response.status_code == 429:
            if attempt < max_retries:
                retry_after = response.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    time.sleep(int(retry_after))
                else:
                    time.sleep(backoffs[attempt])
                continue
            raise RetryLimitError("HTTP 429 retry limit exceeded")

        if 500 <= response.status_code < 600:
            if attempt < max_retries:
                time.sleep(backoffs[attempt])
                continue
            raise RetryLimitError("HTTP 5xx retry limit exceeded")

        response.raise_for_status()
        return response.json()

    raise RetryLimitError("Retry limit exceeded")


def get_candles(inst_id: str, bar: str, limit: int) -> Dict[str, Any]:
    return _request("GET", "/api/v5/market/candles", params={"instId": inst_id, "bar": bar, "limit": str(limit)})


def get_ticker(inst_id: str) -> Dict[str, Any]:
    return _request("GET", "/api/v5/market/ticker", params={"instId": inst_id})


def get_balance(ccy: str = "USDT") -> Dict[str, Any]:
    return _request("GET", "/api/v5/account/balance", params={"ccy": ccy})


def get_positions(inst_id: Optional[str] = None) -> Dict[str, Any]:
    params = {"instId": inst_id} if inst_id else None
    return _request("GET", "/api/v5/account/positions", params=params)


def get_funding_rate(inst_id: str) -> Dict[str, Any]:
    return _request("GET", "/api/v5/public/funding-rate", params={"instId": inst_id})


def get_instruments(inst_type: str = "SWAP", inst_id: str = "BTC-USDT-SWAP") -> Dict[str, Any]:
    raw = _request("GET", "/api/v5/public/instruments", params={"instType": inst_type, "instId": inst_id})
    parsed = raw.copy()
    data = raw.get("data", [])
    if data:
        first = data[0]
        parsed["ctVal"] = first.get("ctVal")
        parsed["lotSz"] = first.get("lotSz")
    return parsed


def place_order(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _request("POST", "/api/v5/trade/order", body=payload)


def cancel_order(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _request("POST", "/api/v5/trade/cancel-order", body=payload)


def cancel_algos(payload: Any) -> Dict[str, Any]:
    """알고 주문 취소. POST /api/v5/trade/cancel-algos"""
    if isinstance(payload, str):
        body = [{"instId": config.SYMBOL, "algoId": payload}]
        return _request("POST", "/api/v5/trade/cancel-algos", body=body)
    return _request("POST", "/api/v5/trade/cancel-algos", body=payload)


def amend_algos(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _request("POST", "/api/v5/trade/amend-algos", body=payload)


def get_algo_orders_pending(ord_type: str = "conditional", inst_type: str = "SWAP") -> Dict[str, Any]:
    return _request("GET", "/api/v5/trade/orders-algo-pending", params={"ordType": ord_type, "instType": inst_type})


def set_leverage(inst_id: str, lever: int, mgn_mode: str = "cross") -> Dict[str, Any]:
    return _request(
        "POST",
        "/api/v5/account/set-leverage",
        body={"instId": inst_id, "lever": str(lever), "mgnMode": mgn_mode},
    )
