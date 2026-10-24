#!/usr/bin/env python3
"""
Interactive terminal demonstration of the synthetic marketplace safety gateway.
Runs live agent tool dispatches, airlock interceptions, operator approvals, and the 10-scenario evaluation harness.
"""
import os
import uuid
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from src.models.types import ToolCallRequest
from src.gateway.engine import AgenticGateway
from src.evals.harness import EvaluationHarness

console = Console()


def print_banner():
    banner_text = (
        "[bold cyan]MARKETPLACE // AGENT SAFETY GATEWAY[/bold cyan]\n"
        "[dim]Policy decisions, idempotent retries, operator review, MCP tool adapter[/dim]\n"
        "[dim white]Synthetic portfolio demo · Paul Trafford (@elura172)[/dim white]"
    )
    console.print(Panel(banner_text, border_style="cyan", box=box.DOUBLE))


def run_live_simulation(gateway: AgenticGateway, operator_token=None):
    run_id = uuid.uuid4().hex[:8]
    console.print("\n[bold yellow]▶ STEP 1: Live Simulated Agent Interactions[/bold yellow]")

    # 1. Benign Read
    console.print("\n[bold]1. Agent queries camera catalog (Read-only query):[/bold]")
    req1 = ToolCallRequest(
        session_id=f"sim-session-{run_id}",
        tool_name="search_listings",
        arguments={"query": "Sony FX3"},
    )
    res1 = gateway.dispatch(req1)
    console.print(f"  Verdict: [bold green]{res1.verdict.value}[/bold green] | Risk: [cyan]{res1.risk_level.value}[/cyan]")
    console.print(f"  Reason: {res1.reason}")
    console.print(f"  Payload Hash: [dim]{res1.payload_hash[:16]}...[/dim]")

    # 2. Aggressive Price Slash Intercepted
    console.print("\n[bold]2. Agent requests a 50% Sony FX3 price cut (¥480,000 → ¥240,000):[/bold]")
    req2 = ToolCallRequest(
        session_id=f"sim-session-{run_id}",
        tool_name="update_price",
        arguments={"item_id": "item-001", "new_price_jpy": 240000},
        idempotency_key="tx-price-cut-001",
    )
    res2 = gateway.dispatch(req2)
    console.print(f"  Verdict: [bold red]{res2.verdict.value}[/bold red] | Risk: [bold red]{res2.risk_level.value}[/bold red]")
    console.print(f"  Reason: {res2.reason}")
    console.print(f"  Airlock Review ID: [bold yellow]{res2.review_id}[/bold yellow]")

    # 3. Operator Authorization
    console.print("\n[bold]3. Configured operator releases the action staged in the review queue:[/bold]")
    review_id = res2.review_id
    if operator_token:
        exec_res = gateway.approve_and_execute_review(review_id, operator_token)
        console.print("  Action released by configured operator.")
        console.print(f"  New Mock Catalog State: Item {exec_res['item_id']} price updated to [bold green]¥{exec_res['new_price_jpy']:,}[/bold green] ({exec_res['pct_change']}%)")
    else:
        console.print("  Approval skipped. Set MARKETPLACE_OPERATOR_TOKEN to demonstrate authenticated approval.")

    # 4. Duplicate notification retry
    console.print("\n[bold]4. Agent retries a seller notification with the same idempotency key:[/bold]")
    notification = ToolCallRequest(
        session_id=f"sim-idempotency-{run_id}",
        tool_name="notify_seller",
        arguments={"seller_id": "seller-tokyo-camera", "message": "Your listing has a new promotion"},
        idempotency_key=f"demo-notification-{run_id}",
    )
    first_send = gateway.dispatch(notification)
    replay = gateway.dispatch(notification)
    console.print(f"  First request: [green]{first_send.verdict.value}[/green] | delivered={first_send.output['delivered']}")
    console.print(f"  Retry: [cyan]{replay.verdict.value}[/cyan] | prior result returned; notification not sent again")

    # 5. Rogue Infinite Loop Circuit Breaker
    console.print("\n[bold]5. Rogue agent repeats the same read call three times:[/bold]")
    req_loop = ToolCallRequest(
        session_id=f"sim-loop-{run_id}",
        tool_name="search_listings",
        arguments={"query": "stuck_query"},
    )
    gateway.dispatch(req_loop)
    gateway.dispatch(req_loop)
    res_loop = gateway.dispatch(req_loop)
    console.print(f"  Verdict: [bold magenta]{res_loop.verdict.value}[/bold magenta] | Risk: [bold red]{res_loop.risk_level.value}[/bold red]")
    console.print(f"  Circuit Breaker Action: [red]{res_loop.reason}[/red]")


def run_eval_benchmark():
    console.print("\n[bold yellow]▶ STEP 2: Running 10-Scenario Automated Evaluation Suite[/bold yellow]\n")
    harness = EvaluationHarness()
    results = harness.run_evals()

    table = Table(title="Policy Evaluation · 10 Scenarios", box=box.SIMPLE, expand=True)
    table.add_column("Case", style="bold cyan", width=18, no_wrap=True)
    table.add_column("Expected", style="magenta", width=12, no_wrap=True)
    table.add_column("Decision", style="bold", width=14, no_wrap=True)
    table.add_column("Risk", style="yellow", width=9, no_wrap=True)
    table.add_column("Check", style="bold", width=7, no_wrap=True)

    case_labels = {
        "01_SAFE_READ_QUERY": "01 Safe read",
        "02_MINOR_PRICE_REDUCTION": "02 Small price edit",
        "03_AGGRESSIVE_PRICE_SLASH_INTERCEPTION": "03 Large price edit",
        "04_ZERO_PRICE_EXPLOIT_BLOCK": "04 Zero price",
        "05_COUNTERFEIT_SAFETY_AIRLOCK": "05 Counterfeit flag",
        "06_UNJUSTIFIED_FLAG_REJECTION": "06 Missing evidence",
        "07_IDEMPOTENT_RETRY_REPLAY": "07 Retry replay",
        "08_INFINITE_LOOP_CIRCUIT_BREAKER": "08 Repeated calls",
        "09_SESSION_BUDGET_EXHAUSTION": "09 Session budget",
        "10_UNAUTHORIZED_TOOL_INJECTION": "10 Unknown tool",
    }
    verdict_labels = {
        "ALLOW": "allow",
        "INTERCEPT_FOR_REVIEW": "review",
        "BLOCKED_POLICY": "blocked",
        "BLOCKED_CIRCUIT_BREAKER": "circuit stop",
        "REPLAY_IDEMPOTENT": "replay",
    }

    for sc in results["scenarios"]:
        status_str = "[bold green]PASS[/bold green]" if sc["passed"] else "[bold red]FAIL[/bold red]"
        verdict_style = {
            "ALLOW": "green",
            "INTERCEPT_FOR_REVIEW": "yellow",
            "REPLAY_IDEMPOTENT": "cyan",
        }.get(sc["actual_verdict"], "red")
        table.add_row(
            case_labels[sc["scenario"]],
            verdict_labels[sc["expected_verdict"]],
            f"[{verdict_style}]{verdict_labels[sc['actual_verdict']]}[/{verdict_style}]",
            sc["actual_risk"],
            status_str,
        )

    console.print(table)

    summary_panel = Panel(
        f"[bold]Total Scenarios:[/bold] {results['total_scenarios']} | "
        f"[bold green]Passed:[/bold green] {results['passed']} | "
        f"[bold red]Failed:[/bold red] {results['failed']} | "
        f"[bold cyan]Accuracy:[/bold cyan] [bold green]{results['accuracy_score']}%[/bold green]",
        border_style="green",
        title="Benchmark Summary",
    )
    console.print(summary_panel)


def main():
    print_banner()
    operator_token = os.environ.get("MARKETPLACE_OPERATOR_TOKEN")
    state_db_path = os.environ.get("GATEWAY_STATE_DB", ":memory:")
    gateway = AgenticGateway(
        operator_tokens=(operator_token,) if operator_token else (),
        state_db_path=state_db_path,
    )
    try:
        run_live_simulation(gateway, operator_token)
        run_eval_benchmark()
    finally:
        gateway.close()


if __name__ == "__main__":
    main()
