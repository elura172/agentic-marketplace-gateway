"""
Agentic Marketplace Gateway — Main Orchestration Engine.
"""
import time
import uuid
import threading
from typing import Any, Dict, Optional, Union
from pathlib import Path
from pydantic import ValidationError
from src.models.types import GateVerdict, RiskLevel, ToolCallRequest, ToolCallResult, TOOL_ARGUMENT_MODELS
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
        operator_tokens=(),
        state_db_path: Union[str, Path] = ":memory:",
    ):
        self.catalog = catalog or MarketplaceCatalog()
        self.policy = policy_engine or PolicyEngine()
        self.airlock = airlock or Airlock(operator_tokens=operator_tokens, database_path=state_db_path)
        self._dispatch_lock = threading.RLock()

    def dispatch(self, request: ToolCallRequest) -> ToolCallResult:
        with self.airlock.transaction(), self._dispatch_lock:
            return self._dispatch_locked(request)

    def _dispatch_locked(self, request: ToolCallRequest) -> ToolCallResult:
        start_time = time.perf_counter()
        call_id = str(uuid.uuid4())
        payload_hash = self.airlock.compute_payload_hash(request)

        argument_model = TOOL_ARGUMENT_MODELS.get(request.tool_name)
        if argument_model is None:
            return self._blocked_result(request, call_id, payload_hash, RiskLevel.CRITICAL,
                                        f"Unrecognized tool '{request.tool_name}' blocked by zero-trust gateway", start_time)
        try:
            validated_arguments = argument_model.model_validate(request.arguments).model_dump()
            request = request.model_copy(update={"arguments": validated_arguments})
        except ValidationError as exc:
            details = "; ".join(error["msg"] for error in exc.errors())
            return self._blocked_result(request, call_id, payload_hash, RiskLevel.HIGH,
                                        f"Invalid arguments: {details}", start_time)

        # 1. Idempotency Check (Fast-Path Replay)
        idempotent_replay = self.airlock.check_idempotency(request, payload_hash)
        if idempotent_replay:
            return idempotent_replay

        # 2. Context Retrieval for Policy Inspection
        catalog_item = None
        if "item_id" in request.arguments:
            catalog_item = self.catalog.get_listing(request.arguments["item_id"])
            if catalog_item is None:
                return self._blocked_result(request, call_id, payload_hash, RiskLevel.HIGH,
                                            f"Listing '{request.arguments['item_id']}' not found", start_time)

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
            self.airlock.record_idempotent_request(request, payload_hash, result)

        return result

    def _blocked_result(self, request, call_id, payload_hash, risk_level, reason, start_time):
        return ToolCallResult(
            call_id=call_id,
            session_id=request.session_id,
            tool_name=request.tool_name,
            verdict=GateVerdict.BLOCKED_POLICY,
            risk_level=risk_level,
            payload_hash=payload_hash,
            reason=reason,
            execution_time_ms=(time.perf_counter() - start_time) * 1000,
        )

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
        with self.airlock.transaction(), self._dispatch_lock:
            review_entry = self.airlock.resolve_review(review_id, operator_token, approved=True)
            self.airlock.mark_review_executing(review_id)
        try:
            with self._dispatch_lock:
                output = self._execute_tool(review_entry["tool_name"], review_entry["arguments"])
            with self.airlock.transaction():
                self.airlock.mark_review_executed(review_id, output)
            return output
        except Exception:
            with self.airlock.transaction():
                self.airlock.mark_review_execution_failed(review_id)
            raise

    def close(self) -> None:
        """Close the Airlock database connection owned by this gateway."""
        self.airlock.close()
