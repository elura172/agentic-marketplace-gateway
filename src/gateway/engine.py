"""
Agentic Marketplace Gateway — Main Orchestration Engine.
"""
import time
import uuid
from typing import Any, Dict, Optional
from src.models.types import GateVerdict, RiskLevel, ToolCallRequest, ToolCallResult
from src.tools.marketplace import MarketplaceCatalog
from src.gateway.policy import PolicyEngine
from src.gateway.airlock import Airlock


class AgenticGateway:
    """
    Unified entry point governing all autonomous agent interactions with marketplace services.
    """
    def __init__(
        self,
        catalog: Optional[MarketplaceCatalog] = None,
        policy_engine: Optional[PolicyEngine] = None,
        airlock: Optional[Airlock] = None,
    ):
        self.catalog = catalog or MarketplaceCatalog()
        self.policy = policy_engine or PolicyEngine()
        self.airlock = airlock or Airlock()

    def dispatch(self, request: ToolCallRequest) -> ToolCallResult:
        start_time = time.perf_counter()
        call_id = str(uuid.uuid4())
        payload_hash = self.airlock.compute_payload_hash(request)

        # 1. Idempotency Check (Fast-Path Replay)
        idempotent_replay = self.airlock.check_idempotency(request, payload_hash)
        if idempotent_replay:
            return idempotent_replay

        # 2. Context Retrieval for Policy Inspection
        catalog_item = None
        if "item_id" in request.arguments:
            catalog_item = self.catalog.get_listing(request.arguments["item_id"])

        # 3. Dynamic Policy & Circuit Breaker Evaluation
        verdict, risk_level, reason = self.policy.evaluate(request, catalog_item)

        # 4. Handle Policy Interceptions & Blocks
        if verdict == GateVerdict.BLOCKED_CIRCUIT_BREAKER or verdict == GateVerdict.BLOCKED_POLICY:
            return ToolCallResult(
                call_id=call_id,
                session_id=request.session_id,
                tool_name=request.tool_name,
                verdict=verdict,
                risk_level=risk_level,
                payload_hash=payload_hash,
                output=None,
                reason=reason,
                execution_time_ms=(time.perf_counter() - start_time) * 1000,
            )

        if verdict == GateVerdict.INTERCEPT_FOR_REVIEW:
            review_id = self.airlock.stage_for_review(request, risk_level, payload_hash, reason)
            return ToolCallResult(
                call_id=call_id,
                session_id=request.session_id,
                tool_name=request.tool_name,
                verdict=verdict,
                risk_level=risk_level,
                payload_hash=payload_hash,
                review_id=review_id,
                reason=reason,
                output={"status": "STAGED_IN_AIRLOCK", "review_id": review_id},
                execution_time_ms=(time.perf_counter() - start_time) * 1000,
            )

        # 5. Autonomous Execution (Verdict == ALLOW)
        output = self._execute_tool(request.tool_name, request.arguments)
        execution_time_ms = (time.perf_counter() - start_time) * 1000

        result = ToolCallResult(
            call_id=call_id,
            session_id=request.session_id,
            tool_name=request.tool_name,
            verdict=verdict,
            risk_level=risk_level,
            payload_hash=payload_hash,
            output=output,
            reason=reason,
            execution_time_ms=execution_time_ms,
        )

        # Record idempotent state if key was provided
        if request.idempotency_key:
            self.airlock.record_idempotent_result(request.idempotency_key, payload_hash, result)

        return result

    def _execute_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if tool_name == "search_listings":
            items = self.catalog.search_listings(
                query=arguments.get("query", ""),
                category=arguments.get("category"),
                max_price=arguments.get("max_price"),
            )
            return {"results": items, "count": len(items)}

        if tool_name == "get_listing":
            item = self.catalog.get_listing(arguments["item_id"])
            if not item:
                raise KeyError(f"Item {arguments['item_id']} not found")
            return item

        if tool_name == "update_price":
            return self.catalog.update_price(
                item_id=arguments["item_id"],
                new_price_jpy=arguments["new_price_jpy"],
            )

        if tool_name == "notify_seller":
            return self.catalog.notify_seller(
                seller_id=arguments["seller_id"],
                message=arguments["message"],
            )

        if tool_name == "flag_item":
            return self.catalog.flag_item(
                item_id=arguments["item_id"],
                violation_type=arguments["violation_type"],
                justification=arguments["justification"],
            )

        raise NotImplementedError(f"Execution for tool '{tool_name}' not implemented")

    def approve_and_execute_review(self, review_id: str, operator_token: str) -> Dict[str, Any]:
        """
        Operator endpoint allowing authenticated human supervisors to release intercepted actions.
        """
        review_entry = self.airlock.resolve_review(review_id, operator_token, approved=True)
        # Execute the original staged tool call
        output = self._execute_tool(review_entry["tool_name"], review_entry["arguments"])
        review_entry["execution_output"] = output
        return output
