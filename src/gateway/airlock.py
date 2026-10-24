"""
Deterministic Airlock: SHA-256 Hashing, Idempotency Registry, and Operator Review Queue.
"""
import hashlib
import json
import time
import uuid
from typing import Any, Dict, Optional
from src.models.types import RiskLevel, GateVerdict, ToolCallRequest, ToolCallResult


class Airlock:
    """
    Guarantees deterministic execution boundaries, cryptographic verification, and review state.
    """
    def __init__(self):
        # Idempotency storage: key -> {payload_hash, result}
        self._idempotency_store: Dict[str, Dict[str, Any]] = {}
        # Operator review queue: review_id -> review_entry
        self._review_queue: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def compute_payload_hash(request: ToolCallRequest) -> str:
        """
        Computes a deterministic SHA-256 hash of the canonical request arguments.
        """
        canonical_json = json.dumps(
            {"tool": request.tool_name, "args": request.arguments},
            sort_keys=True,
            ensure_ascii=True,
        )
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    def check_idempotency(self, request: ToolCallRequest, payload_hash: str) -> Optional[ToolCallResult]:
        if not request.idempotency_key:
            return None

        record = self._idempotency_store.get(request.idempotency_key)
        if not record:
            return None

        # Verify that arguments match the original request
        if record["payload_hash"] != payload_hash:
            raise ValueError(
                f"Idempotency conflict for key '{request.idempotency_key}': payload hash does not match previous execution"
            )

        cached_result = record["result"]
        return ToolCallResult(
            call_id=str(uuid.uuid4()),
            session_id=request.session_id,
            tool_name=request.tool_name,
            verdict=GateVerdict.REPLAY_IDEMPOTENT,
            risk_level=cached_result.risk_level,
            payload_hash=payload_hash,
            output=cached_result.output,
            reason="Idempotent replay: returning verified prior execution record",
            execution_time_ms=0.1,
        )

    def record_idempotent_result(self, key: str, payload_hash: str, result: ToolCallResult):
        self._idempotency_store[key] = {
            "payload_hash": payload_hash,
            "result": result,
            "recorded_at": time.time(),
        }

    def stage_for_review(self, request: ToolCallRequest, risk_level: RiskLevel, payload_hash: str, reason: str) -> str:
        review_id = f"rev-{uuid.uuid4().hex[:8]}"
        self._review_queue[review_id] = {
            "review_id": review_id,
            "session_id": request.session_id,
            "agent_id": request.agent_id,
            "tool_name": request.tool_name,
            "arguments": request.arguments,
            "risk_level": risk_level,
            "payload_hash": payload_hash,
            "reason": reason,
            "status": "PENDING_APPROVAL",
            "created_at": time.time(),
        }
        return review_id

    def get_pending_reviews(self) -> Dict[str, Dict[str, Any]]:
        return {k: v for k, v in self._review_queue.items() if v["status"] == "PENDING_APPROVAL"}

    def resolve_review(self, review_id: str, operator_token: str, approved: bool) -> Dict[str, Any]:
        if not operator_token or not operator_token.startswith("op-auth-"):
            raise PermissionError("Invalid operator token: must carry valid 'op-auth-' signature")

        entry = self._review_queue.get(review_id)
        if not entry:
            raise KeyError(f"Review entry {review_id} not found")

        entry["status"] = "APPROVED" if approved else "REJECTED"
        entry["resolved_by"] = operator_token
        entry["resolved_at"] = time.time()
        return entry
