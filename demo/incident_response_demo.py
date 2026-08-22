"""
demo/incident_response_demo.py — Full Incident Response Graph Demo

Owner: Person A

This script walks through the complete incident response graph end-to-end,
demonstrating:
  1. Normal flow (non-critical incident skips HITL)
  2. HITL flow (critical incident pauses for admin approval)
  3. Ticket flow (simulated tool failure opens a ticket)
  4. Crash-and-resume (kill process mid-run, restart)

Usage:
    python demo/incident_response_demo.py normal      # Non-critical, no HITL
    python demo/incident_response_demo.py hitl        # Critical, pauses at HITL
    python demo/incident_response_demo.py resume_hitl <run_id>  # Admin approves
    python demo/incident_response_demo.py ticket      # Simulated failure -> ticket
    python demo/incident_response_demo.py resume_ticket <run_id> <ticket_id>
"""

import sys
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from state_graph.incident_response import make_incident_response_graph


def demo_normal():
    """Non-critical incident: flows straight through without HITL."""
    run_id = f"normal-demo-{uuid.uuid4().hex[:8]}"
    graph = make_incident_response_graph()

    print(f"[NORMAL] Starting run: {run_id}")
    result = graph.start(run_id, {
        "incident_id": 1,
        "repo": "payments-service",  # matches pr_id=1 below (see db/seed.sql)
        "pr_id": 1,
        "severity": "medium",  # NOT critical -> no HITL
    })

    print(f"[NORMAL] Result: {result}")
    print(f"[NORMAL] Final state: {result.get('node', 'unknown')}")
    print(f"[NORMAL] Run ID for inspection: {run_id}")


def demo_hitl():
    """Critical incident: pauses at HITL node for admin approval."""
    run_id = f"hitl-demo-{uuid.uuid4().hex[:8]}"
    graph = make_incident_response_graph()

    print(f"[HITL] Starting CRITICAL incident run: {run_id}")
    result = graph.start(run_id, {
        "incident_id": 1,
        "repo": "payments-service",  # matches pr_id=1 below (see db/seed.sql)
        "pr_id": 1,
        "severity": "critical",  # Critical -> HITL required
    })

    print(f"[HITL] Result: {result}")
    if result.get("status") == "paused_hitl":
        print(f"[HITL] Graph paused at node: {result.get('node')}")
        print(f"[HITL] Admin must approve via platform UI (http://localhost:8001)")
        print(f"[HITL] OR run: python demo/incident_response_demo.py resume_hitl {run_id}")
    else:
        print(f"[HITL] Unexpected status: {result.get('status')}")

    print(f"[HITL] Run ID: {run_id}")


def demo_resume_hitl(run_id: str):
    """Admin approves a pending HITL task and the graph continues."""
    graph = make_incident_response_graph()

    print(f"[RESUME-HITL] Resuming run: {run_id}")

    # Find the pending HITL task
    from state_graph.store import HitlStore
    hitl_store = HitlStore()
    tasks = hitl_store.list_pending(graph_name="incident_response")
    task = next((t for t in tasks if t.run_id == run_id), None)

    if task is None:
        print(f"[ERROR] No pending HITL task found for run {run_id}")
        sys.exit(1)

    print(f"[RESUME-HITL] Found HITL task {task.id}: {task.reason}")
    print(f"[RESUME-HITL] Approving...")

    # Approve the task
    hitl_store.decide(task.id, approved=True, decided_by="admin", reason="Approved via demo script")

    # Resume graph with the decision
    decision = {"approved": True, "decided_by": "admin", "reason": "Approved via demo script"}
    result = graph.resume(run_id, hitl_decision=decision)

    print(f"[RESUME-HITL] Graph resumed. Result: {result}")
    print(f"[RESUME-HITL] Final state: {result.get('node', 'unknown')}")


def demo_ticket():
    """Simulate a tool failure that opens a ticket."""
    # This would require mocking a tool failure in mcp_adapter
    # For now, show the concept
    print("[TICKET] To demonstrate ticket creation:")
    print("[TICKET] 1. Start a run that reaches deploy_fix")
    print("[TICKET] 2. Temporarily break the MCP tool (e.g., rename the DB file)")
    print("[TICKET] 3. The graph will catch the error and open a ticket")
    print("[TICKET] 4. Fix the tool, then run: python demo/incident_response_demo.py resume_ticket <run_id> <ticket_id>")


def demo_resume_ticket(run_id: str, ticket_id: int):
    """Admin resolves a ticket and the graph resumes from checkpoint."""
    graph = make_incident_response_graph()

    print(f"[RESUME-TICKET] Resolving ticket {ticket_id} for run {run_id}")

    from state_graph.store import TicketStore
    ticket_store = TicketStore()
    ticket_store.set_status(ticket_id, status="resolved", resolution_notes="Fixed via demo script")

    result = graph.resume(run_id)
    print(f"[RESUME-TICKET] Graph resumed. Result: {result}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python demo/incident_response_demo.py normal")
        print("  python demo/incident_response_demo.py hitl")
        print("  python demo/incident_response_demo.py resume_hitl <run_id>")
        print("  python demo/incident_response_demo.py ticket")
        print("  python demo/incident_response_demo.py resume_ticket <run_id> <ticket_id>")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "normal":
        demo_normal()
    elif cmd == "hitl":
        demo_hitl()
    elif cmd == "resume_hitl" and len(sys.argv) >= 3:
        demo_resume_hitl(sys.argv[2])
    elif cmd == "ticket":
        demo_ticket()
    elif cmd == "resume_ticket" and len(sys.argv) >= 4:
        demo_resume_ticket(sys.argv[2], int(sys.argv[3]))
    else:
        print("Invalid command or missing arguments.")
        sys.exit(1)