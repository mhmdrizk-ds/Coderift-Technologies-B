"""
agent/planning_client.py — CLI entry point for the Release Readiness &
Incident Remediation Planning Agent (planning_toolkit/). Matches
agent/client.py's argparse style. Every subcommand prints a real result
computed against the real db/coderift.db — no canned output.

Examples:
    python3 -m agent.planning_client --method decomposition_first \
        --repository billing-worker --candidate-pr-ids 5
    python3 -m agent.planning_client --method dynamic \
        --repository payments-service --candidate-pr-ids 1
    python3 -m agent.planning_client --rank --repository checkout-web --candidate-pr-ids 2 6
    python3 -m agent.planning_client --remediate --repository billing-worker --deployment-id 1
    python3 -m agent.planning_client --remediate --repository payments-service --deployment-id 2
    python3 -m agent.planning_client --reflexion --deployment-ids 1 2
    python3 -m agent.planning_client --compare-divergence
"""
from __future__ import annotations

import argparse
import json
import sys

from planning_toolkit.model_provider import CoderiftChatModel
from planning_toolkit.planning_lab.agent import (
    run_release_readiness_plan,
    run_release_ranking_subtask,
    run_incident_remediation_subtask,
)
from planning_toolkit.planning_lab.algorithms.reflexion import remediate_incident_with_reflexion


def _print_json(payload: dict) -> None:
    print(json.dumps(payload, indent=2, default=str))


def main() -> None:
    parser = argparse.ArgumentParser(description="Coderift Planning Agent CLI")
    parser.add_argument("--method", choices=["decomposition_first", "dynamic"],
                         help="Top-level release-readiness decomposition method.")
    parser.add_argument("--rank", action="store_true",
                         help="Run just the ranking sub-task (auto-routes PS vs. ToT).")
    parser.add_argument("--remediate", action="store_true",
                         help="Run just the remediation sub-task (LATS).")
    parser.add_argument("--reflexion", action="store_true",
                         help="Run the ambiguous-target rollback sub-task (Reflexion).")
    parser.add_argument("--compare-divergence", action="store_true",
                         help="Run planning_toolkit.compare_divergence.")
    parser.add_argument("--repository", type=str, default=None)
    parser.add_argument("--candidate-pr-ids", type=int, nargs="+", default=None)
    parser.add_argument("--deployment-id", type=int, default=None)
    parser.add_argument("--deployment-ids", type=int, nargs="+", default=None)
    args = parser.parse_args()

    llm = CoderiftChatModel()

    if args.compare_divergence:
        from planning_toolkit import compare_divergence
        compare_divergence.main() if hasattr(compare_divergence, "main") else None
        return

    if args.rank:
        if not args.repository or not args.candidate_pr_ids:
            sys.exit("--rank requires --repository and --candidate-pr-ids")
        result = run_release_ranking_subtask(args.repository, args.candidate_pr_ids, llm)
        _print_json({
            "method_used": result.method_used,
            "routing_rationale": result.routing_rationale,
            "output": result.output,
        })
        return

    if args.remediate:
        if not args.repository or args.deployment_id is None:
            sys.exit("--remediate requires --repository and --deployment-id")
        result = run_incident_remediation_subtask(args.repository, args.deployment_id, llm)
        _print_json({
            "method_used": result.method_used,
            "routing_rationale": result.routing_rationale,
            "success": result.success,
            "output": result.output,
        })
        return

    if args.reflexion:
        if not args.deployment_ids:
            sys.exit("--reflexion requires --deployment-ids")
        result = remediate_incident_with_reflexion(
            "An on-call engineer needs to roll back a deployment for further verification.",
            deployment_ids=args.deployment_ids, llm=llm,
        )
        _print_json({
            "success": result.success,
            "output": result.output,
            "trial_count": len(result.trials),
            "trials": [
                {"number": t.number, "attempt": t.attempt, "success": t.feedback.success,
                 "score": t.feedback.score, "reflection": t.reflection}
                for t in result.trials
            ],
        })
        return

    if args.method:
        if not args.repository or not args.candidate_pr_ids:
            sys.exit("--method requires --repository and --candidate-pr-ids")
        method = "decomposition_first" if args.method == "decomposition_first" else "dynamic_decomposition"
        result = run_release_readiness_plan(args.repository, args.candidate_pr_ids, llm, method=method)
        _print_json(result)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
