"""
Model Context Protocol (MCP) Server Interface for the Agentic Marketplace Gateway.
"""
from typing import Any, Dict, List
from src.models.types import ToolCallRequest, GateVerdict
from src.gateway.engine import AgenticGateway


class MarketplaceMCPServer:
    """
    Exposes marketplace tools over the Model Context Protocol schema.
    """
    def __init__(self, gateway: AgenticGateway):
        self.gateway = gateway

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "search_listings",
                "description": "Search active marketplace listings by query, category, or maximum price in JPY.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search keyword or item title"},
                        "category": {"type": "string", "description": "Optional category filter"},
                        "max_price": {"type": "integer", "description": "Maximum price in Japanese Yen (JPY)"},
                    },
                },
            },
            {
                "name": "get_listing",
                "description": "Retrieve comprehensive details for a specific marketplace listing by ID.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "item_id": {"type": "string", "description": "Canonical item identifier (e.g. item-001)"},
                    },
                    "required": ["item_id"],
                },
            },
            {
                "name": "update_price",
                "description": "Adjust the price of a listing. NOTE: Changes exceeding 15% are held in the airlock for review.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "item_id": {"type": "string", "description": "Canonical item identifier"},
                        "new_price_jpy": {"type": "integer", "description": "Target price in Japanese Yen (JPY)"},
                    },
                    "required": ["item_id", "new_price_jpy"],
                },
            },
            {
                "name": "notify_seller",
                "description": "Dispatch an automated transactional or policy notification to a seller.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "seller_id": {"type": "string", "description": "Seller identifier"},
                        "message": {"type": "string", "description": "Notification content"},
                    },
                    "required": ["seller_id", "message"],
                },
            },
            {
                "name": "flag_item",
                "description": "Flag an item for policy violation or counterfeit suspicion. Intercepted for human safety review.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "item_id": {"type": "string", "description": "Item identifier"},
                        "violation_type": {"type": "string", "description": "Violation category (e.g. COUNTERFEIT, PROHIBITED)"},
                        "justification": {"type": "string", "description": "Detailed reasoning and evidence"},
                    },
                    "required": ["item_id", "violation_type", "justification"],
                },
            },
        ]

    def handle_call_tool(self, name: str, arguments: Dict[str, Any], session_id: str = "mcp-session-default", idempotency_key: str = None) -> Dict[str, Any]:
        request = ToolCallRequest(
            session_id=session_id,
            tool_name=name,
            arguments=arguments,
            idempotency_key=idempotency_key,
        )
        result = self.gateway.dispatch(request)
        
        is_error = result.verdict in (GateVerdict.BLOCKED_POLICY, GateVerdict.BLOCKED_CIRCUIT_BREAKER)
        
        return {
            "verdict": result.verdict.value,
            "risk_level": result.risk_level.value,
            "payload_hash": result.payload_hash,
            "review_id": result.review_id,
            "reason": result.reason,
            "content": [
                {
                    "type": "text",
                    "text": str(result.output if result.output is not None else result.reason),
                }
            ],
            "isError": is_error,
        }
