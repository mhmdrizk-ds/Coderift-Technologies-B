import json
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from planning_toolkit.model_provider import CoderiftChatModel
from planning_toolkit.planning_lab.algorithms.decomposition import decompose_goal, execute_plan, final_output

ARTIFACTS_DIR = Path(__file__).resolve().parent.parent / "artifacts"


def run(repository_name: str, candidate_pull_request_ids: list[int]) -> None:
    llm = CoderiftChatModel()
    goal = f"Prepare repository '{repository_name}' for a production release."

    plan = decompose_goal(goal, llm, repository_name=repository_name)
    print(f"Plan: {len(plan.tasks)} tasks, topological order: {plan.topological_order()}")
    print(f"Execution batches (parallel-safe): {plan.execution_batches()}\n")

    outputs = execute_plan(plan, llm, repository_name=repository_name,
                            candidate_pull_request_ids=candidate_pull_request_ids)
    result = final_output(plan, outputs)

    print("FINAL RELEASE PLAN:\n")
    print(result)

    ARTIFACTS_DIR.mkdir(exist_ok=True)
    trace = {
        "agent": "release_readiness_planning",
        "method": "decomposition_first",
        "repository_name": repository_name,
        "plan": plan.model_dump(),
        "topological_order": plan.topological_order(),
        "execution_batches": plan.execution_batches(),
        "node_outputs": outputs,
        "final_output": result,
        "timestamp": time.time(),
    }
    path = ARTIFACTS_DIR / f"decomposition_first_{repository_name}_{int(time.time())}.json"
    path.write_text(json.dumps(trace, indent=2, default=str))
    print(f"\nTrace saved to {path}")

if __name__ == "__main__":
    run(repository_name="billing-worker", candidate_pull_request_ids=[5])

    print("\n" + "=" * 70 + "\n")

    run(repository_name="payments-service", candidate_pull_request_ids=[1])