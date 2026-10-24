"""
Dynamic Policy & Risk Assessment Engine for Marketplace Agent Operations.
"""
from typing import Dict, Any, Tuple, Optional
from src.models.types import RiskLevel, GateVerdict, ToolCallRequest


class PolicyEngine:
    """
    Evaluates agent tool invocations against enterprise risk tiers and bounds.
    """
    def __init__(
        self,
        max_price_delta_pct: float = 15.0,
        max_price_delta_jpy: int = 50000,
        min_allowed_price_jpy: int = 300,
        max_session_tool_calls: int = 8,
    ):
        self.max_price_delta_pct = max_price_delta_pct
        self.max_price_delta_jpy = max_price_delta_jpy
        self.min_allowed_price_jpy = min_allowed_price_jpy
        self.max_session_tool_calls = max_session_tool_calls

        # Session tracking for loop / budget circuit breaker
        self._session_call_counts: Dict[str, int] = {}
        self._session_call_history: Dict[str, list] = {}

    def reset_session(self, session_id: str):
        self._session_call_counts.pop(session_id, None)
        self._session_call_history.pop(session_id, None)

    def check_circuit_breaker(self, request: ToolCallRequest) -> Optional[Tuple[GateVerdict, RiskLevel, str]]:
        session_id = request.session_id
        count = self._session_call_counts.get(session_id, 0) + 1
        self._session_call_counts[session_id] = count

        # 1. Budget iteration cap
        if count > self.max_session_tool_calls:
            return (
                GateVerdict.BLOCKED_CIRCUIT_BREAKER,
                RiskLevel.CRITICAL,
                f"Session {session_id} exceeded maximum iteration budget ({self.max_session_tool_calls} calls)",
            )

        # 2. Infinite loop detection (repeated identical actions)
        history = self._session_call_history.setdefault(session_id, [])
        call_signature = (request.tool_name, frozenset(request.arguments.items()))
        history.append(call_signature)

        if len(history) >= 3 and history[-1] == history[-2] == history[-3]:
            return (
                GateVerdict.BLOCKED_CIRCUIT_BREAKER,
                RiskLevel.CRITICAL,
                f"Agent cycle detected: repeated identical tool call '{request.tool_name}' 3 times consecutively",
            )

        return None

    def evaluate(self, request: ToolCallRequest, catalog_item: Optional[Dict[str, Any]] = None) -> Tuple[GateVerdict, RiskLevel, str]:
        # First check circuit breaker
        breaker = self.check_circuit_breaker(request)
        if breaker:
            return breaker

        tool = request.tool_name
        args = request.arguments

        # Read-only operations are always LOW risk and autonomous
        if tool in ("search_listings", "get_listing"):
            return (GateVerdict.ALLOW, RiskLevel.LOW, "Read-only query approved")

        # Seller notifications are non-destructive
        if tool == "notify_seller":
            msg = args.get("message", "")
            if len(msg.strip()) < 5:
                return (GateVerdict.BLOCKED_POLICY, RiskLevel.MEDIUM, "Notification message empty or malformed")
            return (GateVerdict.ALLOW, RiskLevel.MEDIUM, "Standard merchant notification approved")

        # Price updates require strict percentage and value delta scrutiny
        if tool == "update_price":
            new_price = args.get("new_price_jpy")
            if new_price is None or not isinstance(new_price, int):
                return (GateVerdict.BLOCKED_POLICY, RiskLevel.HIGH, "Invalid or missing 'new_price_jpy' parameter")

            if new_price < self.min_allowed_price_jpy:
                return (
                    GateVerdict.BLOCKED_POLICY,
                    RiskLevel.CRITICAL,
                    f"Price ¥{new_price} violates marketplace minimum threshold (¥{self.min_allowed_price_jpy})",
                )

            if catalog_item:
                curr_price = catalog_item.get("price_jpy", new_price)
                if curr_price > 0:
                    delta_pct = abs((new_price - curr_price) / curr_price) * 100
                    delta_abs = abs(new_price - curr_price)

                    # Intercept significant price modifications for human verification
                    if delta_pct > self.max_price_delta_pct or delta_abs > self.max_price_delta_jpy:
                        return (
                            GateVerdict.INTERCEPT_FOR_REVIEW,
                            RiskLevel.HIGH,
                            f"Price change of {delta_pct:.1f}% (¥{delta_abs:,}) exceeds autonomous threshold ({self.max_price_delta_pct}%)",
                        )

            return (GateVerdict.ALLOW, RiskLevel.MEDIUM, "Price adjustment within autonomous thresholds approved")

        # Item suspension / fraud flags are high risk and must be intercepted
        if tool == "flag_item":
            violation = args.get("violation_type", "UNKNOWN")
            justification = args.get("justification", "")
            if not justification or len(justification) < 10:
                return (GateVerdict.BLOCKED_POLICY, RiskLevel.HIGH, "Flag request lacks substantive justification")
            
            return (
                GateVerdict.INTERCEPT_FOR_REVIEW,
                RiskLevel.HIGH,
                f"Merchant listing suspension for '{violation}' requires operator authorization",
            )

        # Unrecognized tool calls are blocked by default (zero-trust)
        return (GateVerdict.BLOCKED_POLICY, RiskLevel.CRITICAL, f"Unrecognized tool '{tool}' blocked by zero-trust gateway")
