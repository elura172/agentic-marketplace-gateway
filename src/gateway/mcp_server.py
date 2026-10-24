"""
Model Context Protocol (MCP) Server Interface for the Agentic Marketplace Gateway.
"""
from typing import Any, Dict, List, Optional
from src.models.types import GateVerdict, ToolCallRequest, TOOL_ARGUMENT_MODELS
from src.gateway.engine import AgenticGateway


class MarketplaceMCPServer:
    """
    Exposes marketplace tools over the Model Context Protocol schema.
    """
    def __init__(self, gateway: AgenticGateway):
        self.gateway = gateway

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        descriptions = {
            "search_listings": "Search active marketplace listings by query, category, or maximum price in JPY.",
            "get_listing": "Retrieve comprehensive details for a specific marketplace listing by ID.",
            "update_price": "Adjust a listing price. Changes beyond the autonomous threshold are held for review.",
            "notify_seller": "Dispatch an automated transactional or policy notification to a seller.",
            "flag_item": "Flag an item for policy violation or counterfeit suspicion; operator review is required.",
        }
        definitions = []
        for name, argument_model in TOOL_ARGUMENT_MODELS.items():
            input_schema = argument_model.model_json_schema()
            input_schema["additionalProperties"] = False
            definitions.append({
                "name": name,
                "description": descriptions[name],
                "inputSchema": input_schema,
            })
        return definitions

    def handle_call_tool(self, name: str, arguments: Dict[str, Any], session_id: str = "mcp-session-default", idempotency_key: Optional[str] = None) -> Dict[str, Any]:
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
