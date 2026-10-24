"""
Type definitions and schemas for the Agentic Marketplace Gateway.
"""
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class RiskLevel(str, Enum):
    LOW = "LOW"            # Read-only / safe query
    MEDIUM = "MEDIUM"      # Non-destructive mutations (e.g. notifications, minor tag update)
    HIGH = "HIGH"          # Significant state changes (price change >15%, inventory lock)
    CRITICAL = "CRITICAL"  # High-impact irreversible actions (suspensions, refunds, deletions)


class GateVerdict(str, Enum):
    ALLOW = "ALLOW"                                 # Autonomous execution allowed
    INTERCEPT_FOR_REVIEW = "INTERCEPT_FOR_REVIEW"   # Held in airlock awaiting operator token
    BLOCKED_POLICY = "BLOCKED_POLICY"               # Hard policy violation
    BLOCKED_CIRCUIT_BREAKER = "BLOCKED_CIRCUIT_BREAKER" # Agent loop / budget limit exceeded
    REPLAY_IDEMPOTENT = "REPLAY_IDEMPOTENT"         # Returning previous result for idempotent key


class ToolCallRequest(BaseModel):
    session_id: str
    tool_name: str
    arguments: Dict[str, Any]
    idempotency_key: Optional[str] = None
    agent_id: str = "agent-autonomous-1"
    estimated_impact_jpy: Optional[int] = 0


class ToolCallResult(BaseModel):
    call_id: str
    session_id: str
    tool_name: str
    verdict: GateVerdict
    risk_level: RiskLevel
    payload_hash: str
    output: Optional[Dict[str, Any]] = None
    review_id: Optional[str] = None
    reason: Optional[str] = None
    execution_time_ms: float = 0.0


class MarketplaceItem(BaseModel):
    item_id: str
    title: str
    category: str
    price_jpy: int
    seller_id: str
    status: str = "ACTIVE"
    flags: List[str] = Field(default_factory=list)
