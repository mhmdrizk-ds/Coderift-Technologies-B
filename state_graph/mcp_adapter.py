from __future__ import annotations

from typing import Any

from state_graph.contracts import NodeFailure


class McpAdapter:
    def __init__(self, client: Any = None):
        self._client = client or SimulatedMcpClient()

    def draft_incident_summary(self, incident_id: int) -> str:
        try:
            return self._client.call(
                "draft_incident_summary", {"incident_id": incident_id}
            )
        except Exception as exc: 
            raise NodeFailure(
                "DRAFT_SUMMARY_TOOL_ERROR",
                f"draft_incident_summary failed for incident {incident_id}: {exc}",
                payload={"incident_id": incident_id},
            ) from exc

    def deploy_fix(self, repo: str, environment: str, pr_id: int,
                     deployed_by: str) -> dict:
        try:
            return self._client.call(
                "deploy_to_production",
                {
                    "repo": repo,
                    "environment": environment,
                    "pr_id": pr_id,
                    "deployed_by": deployed_by,
                },
            )
        except Exception as exc:
            raise NodeFailure(
                "DEPLOY_FIX_TOOL_ERROR",
                f"deploy_to_production failed for repo={repo} pr={pr_id}: {exc}",
                payload={"repo": repo, "environment": environment, "pr_id": pr_id},
            ) from exc

    def check_deployment_status(self, deployment_id: int) -> dict:
        try:
            return self._client.call(
                "check_deployment_status", {"deployment_id": deployment_id}
            )
        except Exception as exc:
            raise NodeFailure(
                "STATUS_CHECK_TOOL_ERROR",
                f"check_deployment_status failed for deployment {deployment_id}: {exc}",
                payload={"deployment_id": deployment_id},
            ) from exc


class SimulatedMcpClient:
    
    def call(self, tool_name: str, args: dict) -> Any:
        if tool_name == "draft_incident_summary":
            return (f"[simulated] Incident {args['incident_id']}: summary "
                     f"would be drafted here via sampling/createMessage.")
        if tool_name == "deploy_to_production":
            return {
                "deployment_id": 9999,
                "status": "Succeeded",
                "repo": args["repo"],
                "environment": args["environment"],
            }
        if tool_name == "check_deployment_status":
            return {"deployment_id": args["deployment_id"], "status": "Succeeded"}
        raise ValueError(f"SimulatedMcpClient: unknown tool '{tool_name}'")