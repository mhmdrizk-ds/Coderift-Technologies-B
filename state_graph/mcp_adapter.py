from __future__ import annotations

from typing import Any, Optional

from state_graph.contracts import NodeFailure
from mcp_server import db as mcp_db
from mcp_server.auth import Session
from mcp_server.context import ToolContext
from mcp_server.tools_impl.deploy_tools import handle_deploy_to_production
from mcp_server.tools_impl.release_tools import handle_rollback_deployment
from mcp_server.tools_impl.query_tools import handle_check_deployment_status
from mcp_server.tools_impl.incident_tools import handle_draft_incident_summary

class McpAdapter:
    def __init__(self, client: Any = None):
        self._client = client
        self._conn = None
        self._session = None
        self._ctx = None
    
    def _ensure_real_session(self):
        """Lazy-init DB connection + authenticated lead session for real MCP calls."""
        if self._conn is None:
            self._conn = mcp_db.get_connection()
            self._session = Session()
            engineer = mcp_db.get_engineer_by_access_code(self._conn, "ENG-LEAD-01")
            if engineer is None:
                raise RuntimeError(
                    "Lead engineer ENG-LEAD-01 not found in DB. "
                    "Run db/init_db.py to seed the database."
                )
            self._session.login(engineer)
            self._ctx = ToolContext(self._session)

    @staticmethod
    def _extract_text(result: dict) -> str:
        """Extract readable text from an MCP tool result dict."""
        if not isinstance(result, dict):
            return str(result)
        content = result.get("content")
        if isinstance(content, list) and len(content) > 0:
            first = content[0]
            if isinstance(first, dict):
                return first.get("text", str(result))
        if "text" in result:
            return result["text"]
        return str(result)

    def seed_pull_request(self, pull_request_id: int, status: str = "Open",
                            scan_status: Optional[str] = None) -> None:
        """Test/demo convenience — only meaningful against SimulatedMcpClient.
        A live client talks to a real DB and has no notion of seeding."""
        if hasattr(self._client, "seed_pull_request"):
            self._client.seed_pull_request(pull_request_id, status, scan_status)

    def draft_incident_summary(self, incident_id: int) -> str:
        if self._client is not None:
            # Simulated mode (for tests)
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
        # Real MCP call
        self._ensure_real_session()
        try:
            result = handle_draft_incident_summary(
                self._conn, self._session, self._ctx,
                {"incident_id": incident_id}
            )
            return self._extract_text(result)
        except NodeFailure:
            raise
        except Exception as exc:
            raise NodeFailure(
                "DRAFT_SUMMARY_TOOL_ERROR",
                f"draft_incident_summary failed for incident {incident_id}: {exc}",
                payload={"incident_id": incident_id},
            ) from exc

    def deploy_fix(self, repo: str, environment: str, pr_id: int,
                     deployed_by: str) -> dict:
        if self._client is not None:
            # Simulated mode (for tests)
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
        # Real MCP call — FIXED parameter names to match MCP server schema
        self._ensure_real_session()
        try:
            result = handle_deploy_to_production(
                self._conn, self._session, self._ctx,
                {
                    "repository_name": repo,
                    "environment_name": environment,
                    "pull_request_id": pr_id,
                }
            )
            text = self._extract_text(result)
            deployment_id = None
            if isinstance(result, dict) and "deployment_id" in result:
                deployment_id = result["deployment_id"]
            return {
                "deployment_id": deployment_id,
                "status": "Succeeded" if "deployed" in text.lower() else "Failed",
                "repo": repo,
                "environment": environment,
                "details": text,
            }
        except NodeFailure:
            raise
        except Exception as exc:
            raise NodeFailure(
                "DEPLOY_FIX_TOOL_ERROR",
                f"deploy_to_production failed for repo={repo} pr={pr_id}: {exc}",
                payload={"repo": repo, "environment": environment, "pr_id": pr_id},
            ) from exc

    def check_deployment_status(self, deployment_id: int) -> dict:
        if self._client is not None:
            # Simulated mode (for tests)
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
        # Real MCP call
        self._ensure_real_session()
        try:
            dep = self._conn.execute(
                """
                SELECT r.name AS repo_name, e.name AS env_name
                FROM deployments d
                JOIN repositories r ON r.id = d.repository_id
                JOIN environments e ON e.id = d.environment_id
                WHERE d.id = ?
                """,
                (deployment_id,),
            ).fetchone()
            if dep is None:
                return {"deployment_id": deployment_id, "status": "Unknown"}

            result = handle_check_deployment_status(
                self._conn, self._session, self._ctx,
                {
                    "repository_name": dep["repo_name"],
                    "environment_name": dep["env_name"],
                }
            )
            text = self._extract_text(result)
            status = "Succeeded"
            if "Failed" in text:
                status = "Failed"
            elif "RolledBack" in text or "rollback" in text.lower():
                status = "RolledBack"
            elif "Pending" in text:
                status = "Pending"
            elif "InProgress" in text:
                status = "InProgress"
            return {
                "deployment_id": deployment_id,
                "status": status,
                "details": text,
            }
        except NodeFailure:
            raise
        except Exception as exc:
            raise NodeFailure(
                "STATUS_CHECK_TOOL_ERROR",
                f"check_deployment_status failed for deployment {deployment_id}: {exc}",
                payload={"deployment_id": deployment_id},
            ) from exc

    def rollback_deployment(self, deployment_id: int, reason: str) -> dict:
        """Roll back a deployment — used by incident_response graph."""
        if self._client is not None:
            # Simulated mode
            try:
                return self._client.call(
                    "rollback_deployment",
                    {"deployment_id": deployment_id, "reason": reason}
                )
            except Exception as exc:
                raise NodeFailure(
                    "ROLLBACK_TOOL_ERROR",
                    f"rollback_deployment failed for deployment {deployment_id}: {exc}",
                    payload={"deployment_id": deployment_id},
                ) from exc
        # Real MCP call
        self._ensure_real_session()
        try:
            result = handle_rollback_deployment(
                self._conn, self._session, self._ctx,
                {"deployment_id": deployment_id, "reason": reason}
            )
            text = self._extract_text(result)
            return {
                "deployment_id": deployment_id,
                "status": "RolledBack" if "rollback" in text.lower() else "Failed",
                "details": text,
            }
        except NodeFailure:
            raise
        except Exception as exc:
            raise NodeFailure(
                "ROLLBACK_TOOL_ERROR",
                f"rollback_deployment failed for deployment {deployment_id}: {exc}",
                payload={"deployment_id": deployment_id},
            ) from exc        

    # -- security_remediation graph tools -------------------------------
    # These wrap the *real* mcp_server tools (get_pull_request,
    # run_pre_deploy_checks, merge_pull_request, deploy_to_production —
    # see mcp_server/tools_impl/{query,checks,release,deploy}_tools.py).
    # Per security_review_policy.md 6.2, an interrupted run_pre_deploy_checks
    # can leave security_scans inconsistent — that is exactly the failure
    # this method turns into a NodeFailure/ticket, not a silent retry.

    def get_pull_request(self, pull_request_id: int) -> dict:
        try:
            return self._client.call(
                "get_pull_request", {"pull_request_id": pull_request_id}
            )
        except Exception as exc:
            raise NodeFailure(
                "GET_PULL_REQUEST_TOOL_ERROR",
                f"get_pull_request failed for PR {pull_request_id}: {exc}",
                payload={"pull_request_id": pull_request_id},
            ) from exc

    def run_pre_deploy_checks(self, pull_request_id: int) -> dict:
        try:
            return self._client.call(
                "run_pre_deploy_checks", {"pull_request_id": pull_request_id}
            )
        except Exception as exc:
            raise NodeFailure(
                "PRE_DEPLOY_CHECKS_TOOL_ERROR",
                f"run_pre_deploy_checks failed/interrupted for PR "
                f"{pull_request_id}: {exc} (policy 6.2 — may leave "
                f"security_scans inconsistent; do not silently retry).",
                payload={"pull_request_id": pull_request_id},
            ) from exc

    def merge_pull_request(self, pull_request_id: int) -> dict:
        try:
            return self._client.call(
                "merge_pull_request", {"pull_request_id": pull_request_id}
            )
        except Exception as exc:
            raise NodeFailure(
                "MERGE_PULL_REQUEST_TOOL_ERROR",
                f"merge_pull_request failed for PR {pull_request_id}: {exc}",
                payload={"pull_request_id": pull_request_id},
            ) from exc

    def record_review_approval(self, pull_request_id: int) -> dict:
        """No `approve_pull_request` MCP tool exists yet in mcp_server/ —
        code review approval currently has no write path into the
        pull_requests table at all, only merge_pull_request's read-only
        check that status == 'Approved'. That gap is filed as its own
        issue (mcp_server correction, not part of this graph's job).
        Until it lands, this method is the graph's boundary: it is the
        one place the review-approval external event is translated into
        a call an adapter can swap for a real tool later without the
        graph itself changing."""
        try:
            return self._client.call(
                "record_review_approval", {"pull_request_id": pull_request_id}
            )
        except Exception as exc:
            raise NodeFailure(
                "REVIEW_APPROVAL_TOOL_ERROR",
                f"record_review_approval failed for PR {pull_request_id}: {exc}",
                payload={"pull_request_id": pull_request_id},
            ) from exc

    def deploy_to_production_override(self, repository_name: str,
                                        environment_name: str,
                                        pull_request_id: int,
                                        confirmation_note: str) -> dict:
        """Only reachable *after* our own hitl_lead_signoff node has
        already captured a lead's approval — confirmation_note carries
        that decision into the tool call's audit trail (policy 4.2/4.3)."""
        try:
            return self._client.call(
                "deploy_to_production",
                {
                    "repository_name": repository_name,
                    "environment_name": environment_name,
                    "pull_request_id": pull_request_id,
                    "confirmation_note": confirmation_note,
                },
            )
        except Exception as exc:
            raise NodeFailure(
                "DEPLOY_OVERRIDE_TOOL_ERROR",
                f"deploy_to_production override failed for PR "
                f"{pull_request_id}: {exc}",
                payload={"pull_request_id": pull_request_id},
            ) from exc


class SimulatedMcpClient:

    def __init__(self):
        # pull_request_id -> {"status": "Open"/"Approved"/"Merged",
        #                      "scan_status": "Passed"/"Failed"/None}
        # Mirrors the real pull_requests + security_scans tables closely
        # enough for deterministic, repeatable graph tests without a DB.
        self._pull_requests: dict[int, dict] = {}

    def seed_pull_request(self, pull_request_id: int, status: str = "Open",
                            scan_status: Optional[str] = None) -> None:
        self._pull_requests[pull_request_id] = {
            "status": status, "scan_status": scan_status,
        }

    def _pr(self, pull_request_id: int) -> dict:
        return self._pull_requests.setdefault(
            pull_request_id, {"status": "Open", "scan_status": None}
        )

    def call(self, tool_name: str, args: dict) -> Any:
        if tool_name == "draft_incident_summary":
            return (f"[simulated] Incident {args['incident_id']}: summary "
                     f"would be drafted here via sampling/createMessage.")

        if tool_name == "deploy_to_production":
            pr = self._pr(args["pull_request_id"])
            return {
                "deployed": True, "deployment_created": True,
                "deployment_id": 9999, "status": "Succeeded",
                "elicitation_required": pr["scan_status"] != "Passed"
                                          or pr["status"] != "Approved",
            }

        if tool_name == "check_deployment_status":
            return {"deployment_id": args["deployment_id"], "status": "Succeeded"}

        if tool_name == "get_pull_request":
            pr = self._pr(args["pull_request_id"])
            return {
                "pull_request_id": args["pull_request_id"],
                "status": pr["status"],
                "latest_security_scan": (
                    {"status": pr["scan_status"], "scan_type": "SAST"}
                    if pr["scan_status"] else None
                ),
            }

        if tool_name == "run_pre_deploy_checks":
            # Same deterministic rule as the real tool (checks_tools.py):
            # a previously-Failed scan stays Failed until the underlying
            # code actually changes; anything else comes back Passed.
            # A test drives "the code actually changed" by seeding a
            # fresh PR (fix applied) between attempts, exactly like a real
            # patch commit would invalidate the prior scan (policy 5.2).
            pr = self._pr(args["pull_request_id"])
            new_status = "Passed" if pr["scan_status"] != "Failed" else "Failed"
            pr["scan_status"] = new_status
            return {
                "pull_request_id": args["pull_request_id"],
                "final_scan_status": new_status,
            }

        if tool_name == "record_review_approval":
            pr = self._pr(args["pull_request_id"])
            pr["status"] = "Approved"
            return {"pull_request_id": args["pull_request_id"], "status": "Approved"}

        if tool_name == "merge_pull_request":
            # Mirrors release_tools.py's hard gate exactly — no override
            # path exists here, matching the real tool. That's why the
            # graph's Failed-scan override branch calls
            # deploy_to_production_override instead of this method.
            pr = self._pr(args["pull_request_id"])
            if pr["status"] != "Approved":
                raise RuntimeError(
                    f"pull request {args['pull_request_id']} is "
                    f"'{pr['status']}', not Approved; cannot merge"
                )
            if pr["scan_status"] != "Passed":
                raise RuntimeError(
                    f"pull request {args['pull_request_id']}'s latest scan "
                    f"is '{pr['scan_status']}', not Passed; cannot merge"
                )
            pr["status"] = "Merged"
            return {"pull_request_id": args["pull_request_id"], "status": "Merged"}

        raise ValueError(f"SimulatedMcpClient: unknown tool '{tool_name}'")