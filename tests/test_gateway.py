"""
Comprehensive Unit & Integration Test Suite for Agentic Marketplace Gateway.
"""
import pytest
from concurrent.futures import ThreadPoolExecutor
from src.models.types import GateVerdict, RiskLevel, ToolCallRequest
from src.gateway.engine import AgenticGateway
from src.gateway.mcp_server import MarketplaceMCPServer
from src.tools.marketplace import MarketplaceCatalog


@pytest.fixture
def gateway():
    return AgenticGateway(operator_tokens=("test-operator-secret",))


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
    output = gateway.approve_and_execute_review(review_id, "test-operator-secret")
    assert output["status"] == "UPDATED"
    assert output["new_price_jpy"] == 40000
    assert gateway.airlock.get_review(review_id)["status"] == "EXECUTED"
    with pytest.raises(ValueError, match="already executed"):
        gateway.approve_and_execute_review(review_id, "test-operator-secret")


def test_unknown_listing_is_blocked_before_policy_or_execution(gateway):
    req = ToolCallRequest(
        session_id="unknown-listing",
        tool_name="update_price",
        arguments={"item_id": "missing", "new_price_jpy": 1000},
    )
    res = gateway.dispatch(req)
    assert res.verdict == GateVerdict.BLOCKED_POLICY
    assert "not found" in res.reason


def test_boolean_is_not_accepted_as_price(gateway):
    req = ToolCallRequest(
        session_id="bool-price",
        tool_name="update_price",
        arguments={"item_id": "item-002", "new_price_jpy": True},
    )
    res = gateway.dispatch(req)
    assert res.verdict == GateVerdict.BLOCKED_POLICY
    assert "Invalid arguments" in res.reason


def test_idempotency_key_is_scoped_to_session_and_agent(gateway):
    first = ToolCallRequest(
        session_id="tenant-a-session",
        agent_id="agent-a",
        tool_name="notify_seller",
        arguments={"seller_id": "seller-a", "message": "First valid notification"},
        idempotency_key="shared-key",
    )
    second = first.model_copy(update={"session_id": "tenant-b-session", "agent_id": "agent-b"})
    assert gateway.dispatch(first).verdict == GateVerdict.ALLOW
    assert gateway.dispatch(second).verdict == GateVerdict.ALLOW


def test_review_copies_arguments_before_staging(gateway):
    arguments = {"item_id": "item-001", "new_price_jpy": 200000}
    req = ToolCallRequest(session_id="copy-review", tool_name="update_price", arguments=arguments)
    res = gateway.dispatch(req)
    req.arguments["new_price_jpy"] = 300
    output = gateway.approve_and_execute_review(res.review_id, "test-operator-secret")
    assert output["new_price_jpy"] == 200000


def test_concurrent_retries_execute_side_effect_once(gateway):
    request = ToolCallRequest(
        session_id="concurrent-retry",
        tool_name="notify_seller",
        arguments={"seller_id": "seller-a", "message": "One notification only"},
        idempotency_key="concurrent-key",
    )
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: gateway.dispatch(request), range(16)))
    assert sum(result.verdict == GateVerdict.ALLOW for result in results) == 1
    assert sum(result.verdict == GateVerdict.REPLAY_IDEMPOTENT for result in results) == 15
    assert len(gateway.catalog._notifications) == 1


def test_idempotency_survives_gateway_restart(tmp_path):
    db_path = tmp_path / "gateway-state.sqlite3"
    catalog = MarketplaceCatalog()
    request = ToolCallRequest(
        session_id="restart-session",
        tool_name="notify_seller",
        arguments={"seller_id": "seller-a", "message": "Durable retry record"},
        idempotency_key="restart-key",
    )
    first_gateway = AgenticGateway(
        catalog=catalog, state_db_path=db_path, operator_tokens=("test-operator-secret",)
    )
    assert first_gateway.dispatch(request).verdict == GateVerdict.ALLOW
    first_gateway.close()

    restarted_gateway = AgenticGateway(
        catalog=catalog, state_db_path=db_path, operator_tokens=("test-operator-secret",)
    )
    replay = restarted_gateway.dispatch(request)
    assert replay.verdict == GateVerdict.REPLAY_IDEMPOTENT
    assert len(catalog._notifications) == 1
    restarted_gateway.close()


def test_pending_review_survives_gateway_restart(tmp_path):
    db_path = tmp_path / "review-state.sqlite3"
    catalog = MarketplaceCatalog()
    first_gateway = AgenticGateway(
        catalog=catalog, state_db_path=db_path, operator_tokens=("test-operator-secret",)
    )
    staged = first_gateway.dispatch(ToolCallRequest(
        session_id="review-restart",
        tool_name="update_price",
        arguments={"item_id": "item-005", "new_price_jpy": 40000},
    ))
    first_gateway.close()

    restarted_gateway = AgenticGateway(
        catalog=catalog, state_db_path=db_path, operator_tokens=("test-operator-secret",)
    )
    assert staged.review_id in restarted_gateway.airlock.get_pending_reviews()
    output = restarted_gateway.approve_and_execute_review(staged.review_id, "test-operator-secret")
    assert output["new_price_jpy"] == 40000
    assert restarted_gateway.airlock.get_pending_reviews() == {}
    assert restarted_gateway.airlock.get_review(staged.review_id)["status"] == "EXECUTED"
    restarted_gateway.close()


def test_failed_review_execution_is_recorded_and_cannot_be_replayed(gateway):
    staged = gateway.dispatch(ToolCallRequest(
        session_id="failed-review",
        tool_name="update_price",
        arguments={"item_id": "item-005", "new_price_jpy": 40000},
    ))

    def fail_update(**kwargs):
        raise RuntimeError("mock backend unavailable")

    gateway.catalog.update_price = fail_update
    with pytest.raises(RuntimeError, match="backend unavailable"):
        gateway.approve_and_execute_review(staged.review_id, "test-operator-secret")
    assert gateway.airlock.get_review(staged.review_id)["status"] == "EXECUTION_FAILED"
    with pytest.raises(ValueError, match="already execution_failed"):
        gateway.approve_and_execute_review(staged.review_id, "test-operator-secret")


def test_separate_gateways_share_atomic_idempotency_state(tmp_path):
    db_path = tmp_path / "shared-state.sqlite3"
    catalog = MarketplaceCatalog()
    gateways = [
        AgenticGateway(catalog=catalog, state_db_path=db_path),
        AgenticGateway(catalog=catalog, state_db_path=db_path),
    ]
    request = ToolCallRequest(
        session_id="shared-process-session",
        tool_name="notify_seller",
        arguments={"seller_id": "seller-a", "message": "One write across gateways"},
        idempotency_key="shared-process-key",
    )
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda gateway: gateway.dispatch(request), gateways))
        assert sorted(result.verdict.value for result in results) == sorted([
            GateVerdict.ALLOW.value,
            GateVerdict.REPLAY_IDEMPOTENT.value,
        ])
        assert len(catalog._notifications) == 1
    finally:
        for gateway in gateways:
            gateway.close()


def test_idempotency_survives_gateway_restart(tmp_path):
    db_path = tmp_path / "gateway-state.sqlite3"
    catalog = MarketplaceCatalog()
    request = ToolCallRequest(
        session_id="restart-session",
        tool_name="notify_seller",
        arguments={"seller_id": "seller-a", "message": "Durable retry record"},
        idempotency_key="restart-key",
    )
    first_gateway = AgenticGateway(
        catalog=catalog, state_db_path=db_path, operator_tokens=("test-operator-secret",)
    )
    assert first_gateway.dispatch(request).verdict == GateVerdict.ALLOW
    first_gateway.close()

    restarted_gateway = AgenticGateway(
        catalog=catalog, state_db_path=db_path, operator_tokens=("test-operator-secret",)
    )
    replay = restarted_gateway.dispatch(request)
    assert replay.verdict == GateVerdict.REPLAY_IDEMPOTENT
    assert len(catalog._notifications) == 1
    restarted_gateway.close()


def test_pending_review_survives_gateway_restart(tmp_path):
    db_path = tmp_path / "review-state.sqlite3"
    catalog = MarketplaceCatalog()
    first_gateway = AgenticGateway(
        catalog=catalog, state_db_path=db_path, operator_tokens=("test-operator-secret",)
    )
    staged = first_gateway.dispatch(ToolCallRequest(
        session_id="review-restart",
        tool_name="update_price",
        arguments={"item_id": "item-005", "new_price_jpy": 40000},
    ))
    first_gateway.close()

    restarted_gateway = AgenticGateway(
        catalog=catalog, state_db_path=db_path, operator_tokens=("test-operator-secret",)
    )
    assert staged.review_id in restarted_gateway.airlock.get_pending_reviews()
    output = restarted_gateway.approve_and_execute_review(staged.review_id, "test-operator-secret")
    assert output["new_price_jpy"] == 40000
    assert restarted_gateway.airlock.get_pending_reviews() == {}
    restarted_gateway.close()


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
    assert all(tool["inputSchema"]["additionalProperties"] is False for tool in tool_defs)


def test_mcp_schema_and_runtime_validation_share_the_same_models(mcp_server):
    from src.models.types import TOOL_ARGUMENT_MODELS

    definitions = {tool["name"]: tool["inputSchema"] for tool in mcp_server.get_tool_definitions()}
    assert set(definitions) == set(TOOL_ARGUMENT_MODELS)
    for name, model in TOOL_ARGUMENT_MODELS.items():
        assert set(definitions[name]["properties"]) == set(model.model_fields)
