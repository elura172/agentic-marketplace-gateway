"""
Comprehensive Unit & Integration Test Suite for Agentic Marketplace Gateway.
"""
import pytest
from src.models.types import GateVerdict, RiskLevel, ToolCallRequest
from src.gateway.engine import AgenticGateway
from src.gateway.mcp_server import MarketplaceMCPServer


@pytest.fixture
def gateway():
    return AgenticGateway()


@pytest.fixture
def mcp_server(gateway):
    return MarketplaceMCPServer(gateway)


def test_safe_search_listings(gateway):
    req = ToolCallRequest(
        session_id="test-s1",
        tool_name="search_listings",
        arguments={"query": "Sony"},
    )
    res = gateway.dispatch(req)
    assert res.verdict == GateVerdict.ALLOW
    assert res.risk_level == RiskLevel.LOW
    assert "count" in res.output
    assert res.output["count"] >= 1


def test_modest_price_update(gateway):
    req = ToolCallRequest(
        session_id="test-s2",
        tool_name="update_price",
        arguments={"item_id": "item-002", "new_price_jpy": 30000},
    )
    res = gateway.dispatch(req)
    assert res.verdict == GateVerdict.ALLOW
    assert res.risk_level == RiskLevel.MEDIUM
    assert res.output["status"] == "UPDATED"


def test_aggressive_price_update_interception(gateway):
    req = ToolCallRequest(
        session_id="test-s3",
        tool_name="update_price",
        arguments={"item_id": "item-001", "new_price_jpy": 200000}, # 58% slash
    )
    res = gateway.dispatch(req)
    assert res.verdict == GateVerdict.INTERCEPT_FOR_REVIEW
    assert res.risk_level == RiskLevel.HIGH
    assert res.review_id is not None
    assert "exceeds autonomous threshold" in res.reason


def test_zero_price_blocked(gateway):
    req = ToolCallRequest(
        session_id="test-s4",
        tool_name="update_price",
        arguments={"item_id": "item-001", "new_price_jpy": 0},
    )
    res = gateway.dispatch(req)
    assert res.verdict == GateVerdict.BLOCKED_POLICY
    assert res.risk_level == RiskLevel.CRITICAL


def test_counterfeit_flag_airlock(gateway):
    req = ToolCallRequest(
        session_id="test-s5",
        tool_name="flag_item",
        arguments={
            "item_id": "item-004",
            "violation_type": "COUNTERFEIT",
            "justification": "Serial tag fails optical stitching verification check",
        },
    )
    res = gateway.dispatch(req)
    assert res.verdict == GateVerdict.INTERCEPT_FOR_REVIEW
    assert res.risk_level == RiskLevel.HIGH
    assert res.review_id is not None


def test_operator_review_approval(gateway):
    req = ToolCallRequest(
        session_id="test-s6",
        tool_name="update_price",
        arguments={"item_id": "item-005", "new_price_jpy": 40000}, # Big drop
    )
    res = gateway.dispatch(req)
    assert res.verdict == GateVerdict.INTERCEPT_FOR_REVIEW
    review_id = res.review_id

    # Invalid operator token should fail
    with pytest.raises(PermissionError):
        gateway.approve_and_execute_review(review_id, "invalid-token")

    # Valid operator token releases action
    output = gateway.approve_and_execute_review(review_id, "op-auth-lead-9")
    assert output["status"] == "UPDATED"
    assert output["new_price_jpy"] == 40000


def test_idempotency_replay(gateway):
    key = "idem-test-key-999"
    req = ToolCallRequest(
        session_id="test-s7",
        tool_name="notify_seller",
        arguments={"seller_id": "seller-tokyo-camera", "message": "Your item was featured on the homepage"},
        idempotency_key=key,
    )
    res1 = gateway.dispatch(req)
    assert res1.verdict == GateVerdict.ALLOW

    # Repeat with same key
    res2 = gateway.dispatch(req)
    assert res2.verdict == GateVerdict.REPLAY_IDEMPOTENT
    assert res2.output["delivered"] is True


def test_idempotency_conflict(gateway):
    key = "idem-conflict-key-1"
    req1 = ToolCallRequest(
        session_id="test-s8",
        tool_name="notify_seller",
        arguments={"seller_id": "seller-1", "message": "Original message"},
        idempotency_key=key,
    )
    gateway.dispatch(req1)

    req2 = ToolCallRequest(
        session_id="test-s8",
        tool_name="notify_seller",
        arguments={"seller_id": "seller-1", "message": "Tampered different message"},
        idempotency_key=key,
    )
    with pytest.raises(ValueError, match="Idempotency conflict"):
        gateway.dispatch(req2)


def test_circuit_breaker_loop_detection(gateway):
    session = "loop-test-sess"
    req = ToolCallRequest(
        session_id=session,
        tool_name="search_listings",
        arguments={"query": "stuck"},
    )
    res1 = gateway.dispatch(req)
    assert res1.verdict == GateVerdict.ALLOW
    res2 = gateway.dispatch(req)
    assert res2.verdict == GateVerdict.ALLOW
    res3 = gateway.dispatch(req)
    assert res3.verdict == GateVerdict.BLOCKED_CIRCUIT_BREAKER
    assert "Agent cycle detected" in res3.reason


def test_mcp_server_interface(mcp_server):
    tool_defs = mcp_server.get_tool_definitions()
    assert len(tool_defs) >= 5
    tool_names = [t["name"] for t in tool_defs]
    assert "search_listings" in tool_names
    assert "update_price" in tool_names
    assert "flag_item" in tool_names

    call_res = mcp_server.handle_call_tool(
        name="search_listings",
        arguments={"query": "MacBook"},
    )
    assert call_res["verdict"] == "ALLOW"
    assert call_res["isError"] is False
