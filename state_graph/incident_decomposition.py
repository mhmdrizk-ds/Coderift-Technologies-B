from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pydantic import BaseModel, ConfigDict

from planning_toolkit.model_provider import CoderiftChatModel, NoLiveModelConfigured
from planning_toolkit.planning_lab.models import Plan, Task

INCIDENT_PLANNER_SYSTEM = """You are a careful task-decomposition planner for the \
Incident Response agent at Coderift Technologies. Produce a small executable DAG \
of remediation steps for ONE specific incident, not a prose checklist. The DAG \
must be groundable against the incident facts and the incident response runbook \
given to you — do not invent facts not present in them. Every task needs a short \
snake_case id, a concrete one-sentence instruction, and depends_on listing the ids \
of tasks that must complete first. Between 3 and 6 tasks."""


class GeneratedIncidentPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tasks: list[Task]


def _fixed_incident_plan(incident_facts: dict) -> Plan:
    severity = incident_facts.get("severity")
    deployment_id = incident_facts.get("deployment_id")
    deployment_status = incident_facts.get("deployment_status")
    repository_name = incident_facts.get("repository_name")

    tasks: list[dict] = [
        {
            "id": "confirm_scope",
            "instruction": (
                f"Confirm the failing deployment (#{deployment_id}, status "
                f"{deployment_status!r}) and affected repository/environment "
                f"({repository_name}) via get_incident."
            ),
            "depends_on": [],
        },
    ]

    if severity == "critical":
        tasks.append({
            "id": "page_on_call_lead",
            "instruction": "Page the platform on-call lead immediately, before scope is fully assessed (runbook 3.1).",
            "depends_on": ["confirm_scope"],
        })
        tasks.append({
            "id": "halt_new_deployments",
            "instruction": "Halt new deployments to this repository until the incident's cause is identified (runbook 4.1).",
            "depends_on": ["confirm_scope"],
        })

    if deployment_id is not None:
        if deployment_status in ("Succeeded", "InProgress"):
            tasks.append({
                "id": "roll_back_deployment",
                "instruction": f"Roll back deployment #{deployment_id} via rollback_deployment — remediation is legal during a halt (runbook 4.3).",
                "depends_on": ["confirm_scope"],
            })
        else:
            tasks.append({
                "id": "investigate_terminal_deployment",
                "instruction": (
                    f"Deployment #{deployment_id} is already {deployment_status!r} — a terminal "
                    "state. Do not retry rollback; investigate root cause instead "
                    "(production_deployment_policy.md 6.3)."
                ),
                "depends_on": ["confirm_scope"],
            })

    remediation_dep = next(
        (t["id"] for t in tasks if t["id"] in ("roll_back_deployment", "investigate_terminal_deployment")),
        "confirm_scope",
    )
    tasks.append({
        "id": "monitor_post_remediation",
        "instruction": "Verify with external monitoring after remediation — a Succeeded status alone does not mean the service is healthy (runbook Section 8).",
        "depends_on": [remediation_dep],
    })

    return Plan.model_validate({
        "goal": f"Remediate incident #{incident_facts.get('incident_id')}: {incident_facts.get('title')}",
        "tasks": tasks,
    })


def decompose_remediation(incident_facts: dict, runbook_context: list[dict] | None = None,
                           llm: CoderiftChatModel | None = None) -> Plan:
    llm = llm or CoderiftChatModel()
    incident_id = incident_facts.get("incident_id")
    title = incident_facts.get("title", "")

    context_text = "\n".join(
        f"- {c['section']}: {c['excerpt']}" for c in (runbook_context or [])
    ) or "(no runbook sections retrieved)"

    prompt = (
        f"Incident #{incident_id}: {title!r}\n"
        f"Facts: {incident_facts}\n\n"
        f"Relevant runbook sections:\n{context_text}\n\n"
        "Produce the remediation task DAG."
    )

    try:
        generated = llm.with_structured_output(GeneratedIncidentPlan, method="json_schema").invoke(
            [("system", INCIDENT_PLANNER_SYSTEM), ("human", prompt)],
            temperature=0.1,
        )
        return Plan.model_validate({
            "goal": f"Remediate incident #{incident_id}: {title}",
            "tasks": [t.model_dump() for t in generated.tasks],
        })
    except (NoLiveModelConfigured, Exception):
        return _fixed_incident_plan(incident_facts)