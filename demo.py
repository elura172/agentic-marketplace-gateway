#!/usr/bin/env python3
"""
Interactive Terminal Demo for Mercari: The Agentic Marketplace Gateway.
Runs live agent tool dispatches, airlock interceptions, operator approvals, and the 10-scenario evaluation harness.
"""
import sys
import time
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from src.models.types import ToolCallRequest, GateVerdict, RiskLevel
from src.gateway.engine import AgenticGateway
from src.evals.harness import EvaluationHarness

console = Console()


def print_banner():
    banner_text = (
        "[bold cyan]MERCARI // AGENTIC MARKETPLACE GATEWAY[/bold cyan]\n"
        "[dim]Deterministic Safety Airlock, Dynamic Policy Engine & Model Context Protocol (MCP)[/dim]\n"
        "[dim white]Author: Paul Trafford (elura172) · Tokyo Relocation Pipeline Candidate[/dim white]"
    )
    console.print(Panel(banner_text, border_style="cyan", box=box.DOUBLE))


def run_live_simulation(gateway: AgenticGateway):
    console.print("\n[bold yellow]▶ STEP 1: Live Simulated Agent Interactions[/bold yellow]")

    # 1. Benign Read
    console.print("\n[bold]1. Agent queries camera catalog (Read-only query):[/bold]")
    req1 = ToolCallRequest(
        session_id="sim-session-1",
        tool_name="search_listings",
        arguments={"query": "Sony FX3"},
    )
    res1 = gateway.dispatch(req1)
    console.print(f"  Verdict: [bold green]{res1.verdict.value}[/bold green] | Risk: [cyan]{res1.risk_level.value}[/cyan]")
    console.print(f"  Reason: {res1.reason}")
    console.print(f"  Payload Hash: [dim]{res1.payload_hash[:16]}...[/dim]")

    # 2. Aggressive Price Slash Intercepted
    console.print("\n[bold]2. Agent attempts aggressive 50% price reduction on Sony FX3 (¥480,000 -> ¥240,000):[/bold]")
    req2 = ToolCallRequest(
        session_id="sim-session-1",
        tool_name="update_price",
        arguments={"item_id": "item-001", "new_price_jpy": 240000},
        idempotency_key="tx-price-cut-001",
    )
    res2 = gateway.dispatch(req2)
    console.print(f"  Verdict: [bold red]{res2.verdict.value}[/bold red] | Risk: [bold red]{res2.risk_level.value}[/bold red]")
    console.print(f"  Reason: {res2.reason}")
    console.print(f"  Airlock Review ID: [bold yellow]{res2.review_id}[/bold yellow]")

    # 3. Operator Authorization
    console.print("\n[bold]3. Human Supervisor inspects airlock and releases action with operator token:[/bold]")
    review_id = res2.review_id
    operator_token = "op-auth-tokyo-supervisor-7"
    exec_res = gateway.approve_and_execute_review(review_id, operator_token)
    console.print(f"  Action Released by: [green]{operator_token}[/green]")
    console.print(f"  New Database State: Item {exec_res['item_id']} price updated to [bold green]¥{exec_res['new_price_jpy']:,}[/bold green] ({exec_res['pct_change']}%)")

    # 4. Rogue Infinite Loop Circuit Breaker
    console.print("\n[bold]4. Rogue agent enters infinite retry cycle (3 identical calls):[/bold]")
    req_loop = ToolCallRequest(
        session_id="sim-loop-session",
        tool_name="search_listings",
        arguments={"query": "stuck_query"},
    )
    gateway.dispatch(req_loop)
    gateway.dispatch(req_loop)
    res_loop = gateway.dispatch(req_loop)
    console.print(f"  Verdict: [bold magenta]{res_loop.verdict.value}[/bold magenta] | Risk: [bold red]{res_loop.risk_level.value}[/bold red]")
    console.print(f"  Circuit Breaker Action: [red]{res_loop.reason}[/red]")


def run_eval_benchmark(gateway: AgenticGateway):
    console.print("\n[bold yellow]▶ STEP 2: Running 10-Scenario Automated Evaluation Suite[/bold yellow]\n")
    harness = EvaluationHarness(gateway)
    results = harness.run_evals()

    table = Table(title="Gateway Policy & Safety Benchmark Results", box=box.ROUNDED)
    table.add_column("Scenario ID", style="bold cyan")
    table.add_column("Description", style="white")
    table.add_column("Expected Verdict", style="magenta")
    table.add_column("Actual Verdict", style="bold")
    table.add_column("Risk Tier", style="yellow")
    table.add_column("Status", style="bold")

    for sc in results["scenarios"]:
        status_str = "[bold green]PASS[/bold green]" if sc["passed"] else "[bold red]FAIL[/bold red]"
        verdict_style = "green" if sc["actual_verdict"] == "ALLOW" else ("yellow" if "INTERCEPT" in sc["actual_verdict"] else "red")
        table.add_row(
            sc["scenario"],
            sc["description"][:45] + "...",
            sc["expected_verdict"],
            f"[{verdict_style}]{sc['actual_verdict']}[/{verdict_style}]",
            sc["actual_risk"],
            status_str,
        )

    console.print(table)

    summary_panel = Panel(
        f"[bold]Total Scenarios:[/bold] {results['total_scenarios']} | "
        f"[bold green]Passed:[/bold green] {results['passed']} | "
        f"[bold red]Failed:[/bold red] {results['failed']} | "
        f"[bold cyan]Accuracy Score:[/bold cyan] [bold green]{results['accuracy_score']}%[/bold green] | "
        f"[bold yellow]Avg Latency:[/bold yellow] {results['average_latency_ms']} ms",
        border_style="green",
        title="Benchmark Summary",
    )
    console.print(summary_panel)


def main():
    print_banner()
    gateway = AgenticGateway()
    run_live_simulation(gateway)
    time.sleep(0.5)
    run_eval_benchmark(gateway)


if __name__ == "__main__":
    main()
