import argparse
import json
import subprocess
import sys
from pathlib import Path

from agent.elicitation import interactive_elicitation_handler, scripted_elicitation_handler
from agent.progress import progress_handler
from agent.sampling import sampling_handler
from agent.scenarios import SCENARIO_ORDER, SCENARIOS, READ_ONLY_SCENARIOS
from agent.session import CoderiftAgentSession

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEST_INPUTS_PATH = Path(__file__).resolve().parent / "test_inputs.json"
DEFAULT_ELICITATION_ANSWER = {"action": "accept", "content": {"confirm": True}}


def load_test_inputs():
    with open(TEST_INPUTS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["scenarios"]


def rebuild_database():
    """Run db/init_db.py so every full demo run starts from the same fixed
    seed state — no reliance on lucky random data or leftover writes from
    a previous run."""
    print(">>> Rebuilding database from db/schema.sql + db/seed.sql ...")
    subprocess.run([sys.executable, str(PROJECT_ROOT / "db" / "init_db.py")], check=True)


def build_session(name, scenario_data, interactive):
    profile = "read_only" if name in READ_ONLY_SCENARIOS else "full"

    if interactive:
        elicit = interactive_elicitation_handler
    else:
        fixed = scenario_data.get("elicitation_response", DEFAULT_ELICITATION_ANSWER)
        elicit = scripted_elicitation_handler(fixed)

    # A handful of tool calls in a short demo scenario would never overflow
    # the real 50-message default buffer, so nothing would reach the
    # router. A small buffer here demonstrates the same pipeline
    # (buffer -> router -> episodic -> consolidation) without needing 50+
    # calls — see agent/scenarios.py's memory_recall_in_session docstring.
    memory_buffer_capacity = 3 if name == "memory_recall_in_session" else 50

    return CoderiftAgentSession(
        elicitation_handler=elicit,
        sampling_handler=sampling_handler,
        progress_handler=progress_handler,
        capability_profile=profile,
        memory_buffer_capacity=memory_buffer_capacity,
    )


def run_scenario(name, all_data, interactive):
    if name not in SCENARIOS:
        print(f"Unknown scenario '{name}'. Use --list to see valid names.", file=sys.stderr)
        sys.exit(1)

    data = all_data[name]
    print(f"\n>>> Running scenario: {name}")
    print(f">>> {data.get('description', '')}")

    session = build_session(name, data, interactive)
    try:
        session.initialize()
        SCENARIOS[name](session, data)
    except Exception as exc:  # noqa: BLE001 - surface it, then still close cleanly
        print(f"\n!!! Scenario '{name}' raised: {exc}", file=sys.stderr)
        raise
    finally:
        session.close()


def main():
    parser = argparse.ArgumentParser(description="Coderift Technologies MCP agent")
    parser.add_argument("--scenario", help="Name of a single scenario to run")
    parser.add_argument("--all", action="store_true", help="Rebuild the DB, then run all scenarios in order")
    parser.add_argument("--list", action="store_true", help="List available scenarios and exit")
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Answer elicitation prompts live at the terminal instead of using the scripted demo answers",
    )
    parser.add_argument(
        "--no-rebuild",
        action="store_true",
        help="Skip the automatic db rebuild before --all (reuse whatever db/coderift.db currently has)",
    )
    args = parser.parse_args()

    all_data = load_test_inputs()

    if args.list:
        for name in SCENARIO_ORDER:
            print(f"  {name:42s} {all_data[name].get('description', '')}")
        return

    if args.all:
        if not args.no_rebuild:
            rebuild_database()
        for name in SCENARIO_ORDER:
            run_scenario(name, all_data, args.interactive)
        return

    if args.scenario:
        run_scenario(args.scenario, all_data, args.interactive)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
