TOOLS = [
    {
        "name": "get_candles",
        "description": "특정 타임프레임의 캔들 데이터를 요청한다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "timeframe": {"type": "string", "enum": ["1D", "4H", "1H", "15m"]},
                "count": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "required": ["timeframe", "count"],
        },
    },
    {
        "name": "save_analysis",
        "description": "시장 분석 결과를 저장한다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "direction": {"type": "string", "enum": ["bullish", "bearish", "neutral"]},
                "key_levels": {
                    "type": "object",
                    "properties": {
                        "support": {"type": "array", "items": {"type": "number"}},
                        "resistance": {"type": "array", "items": {"type": "number"}},
                    },
                },
                "summary": {"type": "string"},
                "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
            },
            "required": ["direction", "summary"],
        },
    },
    {
        "name": "save_scenario",
        "description": "트레이딩 시나리오를 저장한다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "scenario_id": {"type": "string"},
                "description": {"type": "string"},
                "direction": {"type": "string", "enum": ["long", "short"]},
                "entry_zone": {
                    "type": "object",
                    "properties": {
                        "min": {"type": "number"},
                        "max": {"type": "number"},
                    },
                },
                "sl_price": {"type": "number"},
                "tp_prices": {"type": "array", "items": {"type": "number"}},
                "valid_condition": {"type": "string"},
            },
            "required": ["scenario_id", "description", "direction"],
        },
    },
    {
        "name": "set_alert",
        "description": "가격 알림을 설정한다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "price": {"type": "number"},
                "direction": {"type": "string", "enum": ["above", "below"]},
                "scenario_id": {"type": "string"},
                "valid_condition": {"type": "string"},
            },
            "required": ["price", "direction"],
        },
    },
    {
        "name": "remove_alert",
        "description": "기존 가격 알림을 제거한다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "alert_id": {"type": "string"},
            },
            "required": ["alert_id"],
        },
    },
    {
        "name": "open_position",
        "description": "새 포지션을 연다. SL 필수. TP 선택.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["open_long", "open_short"]},
                "ordType": {"type": "string", "enum": ["market", "limit"], "default": "market"},
                "px": {"type": "number", "description": "지정가 주문 시 가격. ordType=limit일 때 필수."},
                "sl_price": {"type": "number"},
                "tp_price": {"type": "number"},
                "acceptable_price_range": {
                    "type": "object",
                    "properties": {
                        "min": {"type": "number"},
                        "max": {"type": "number"},
                    },
                },
                "override_principle_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "무시할 원칙 ID 목록. block_suggest 원칙을 override할 때 사용.",
                },
                "reason": {"type": "string"},
            },
            "required": ["action", "sl_price", "reason"],
        },
    },
    {
        "name": "close_position",
        "description": "현재 포지션을 청산한다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "close_percent": {"type": "number", "minimum": 0, "maximum": 100, "default": 100},
                "reason": {"type": "string"},
            },
            "required": ["reason"],
        },
    },
    {
        "name": "modify_sl",
        "description": "기존 SL 가격을 수정한다. 유리한 방향으로만 가능.",
        "input_schema": {
            "type": "object",
            "properties": {
                "new_sl_price": {"type": "number"},
                "reason": {"type": "string"},
            },
            "required": ["new_sl_price", "reason"],
        },
    },
    {
        "name": "modify_tp",
        "description": "TP 가격을 수정, 추가, 또는 제거한다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "new_tp_price": {"type": "number", "description": "0이면 TP 제거"},
                "reason": {"type": "string"},
            },
            "required": ["reason"],
        },
    },
    {
        "name": "save_trade_memo",
        "description": "거래에 대한 자유형 메모를 저장한다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "trade_id": {"type": "string"},
                "memo": {"type": "string"},
            },
            "required": ["trade_id", "memo"],
        },
    },
    {
        "name": "save_insight",
        "description": "인사이트를 생성, 아카이브, 또는 폐기한다. create 시 origin_trades, key_observation 필수. archive/invalidate 시 insight_id, reason 필수.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["create", "archive", "invalidate"], "default": "create"},
                "content": {"type": "string"},
                "category": {"type": "string", "enum": ["entry", "exit", "risk", "market", "general"]},
                "supporting_data": {"type": "string", "description": "이 인사이트를 뒷받침하는 통계 요약"},
                "origin_trades": {"type": "array", "items": {"type": "string"}, "description": "근거 거래 ID 목록"},
                "key_observation": {"type": "string", "description": "핵심 관찰"},
                "failed_attempts": {"type": "array", "items": {"type": "object"}, "description": "실패 경험 목록"},
                "supersedes": {"type": "string", "description": "대체하는 기존 인사이트 ID"},
                "trigger_conditions": {"type": "object", "description": "트리거 조건 (env_match, action_match, indicator_match)"},
                "insight_id": {"type": "string", "description": "archive/invalidate 대상 ID"},
                "reason": {"type": "string", "description": "archive/invalidate 사유"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "save_principle",
        "description": "검증된 인사이트를 트레이딩 원칙으로 승격한다. trigger_conditions 필수.",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "based_on_insight_id": {"type": "string"},
                "trigger_conditions": {"type": "object", "description": "트리거 조건 (env_match, action_match)"},
                "alert_level": {"type": "string", "enum": ["info", "block_suggest"], "default": "block_suggest"},
            },
            "required": ["content", "trigger_conditions"],
        },
    },
    {
        "name": "update_analysis_routine",
        "description": "지표의 활성 여부와 파라미터를 변경한다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "indicator": {"type": "string"},
                "active": {"type": "boolean"},
                "params": {"type": "object"},
            },
            "required": ["indicator", "active"],
        },
    },
    {
        "name": "conclude_review",
        "description": "복기를 완료하고 남은 iteration을 건너뛴다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
            },
            "required": ["summary"],
        },
    },
    {
        "name": "send_telegram",
        "description": "사용자에게 텔레그램 메시지를 보낸다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string"},
            },
            "required": ["message"],
        },
    },
    {
        "name": "get_insight_detail",
        "description": "특정 인사이트의 전체 reasoning_chain을 조회한다. 결과는 5분간 캐시된다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "insight_id": {"type": "string"}
            },
            "required": ["insight_id"],
        },
    },
]
