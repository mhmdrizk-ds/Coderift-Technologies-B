from __future__ import annotations

import json

from mcp_server import db

from ..models import EnvironmentFeedback


class Environment:
    def __init__(self):
        pass
    def evaluate(self, state: str) -> EnvironmentFeedback:
        if not isinstance(state, str):
            raise TypeError("state must be a string")

        try:
            payload = json.loads(state)
        except json.JSONDecodeError as exc:
            return EnvironmentFeedback(
                success=False, score=0.0,
                details=[f"state is not valid JSON: {exc}"],
            )

        action = payload.get("action")
        handlers = {
            "deploy_pr": self._evaluate_deploy_pr,
            "merge_pr": self._evaluate_merge_pr,
            "rollback_deployment": self._evaluate_rollback_deployment,
            "release_plan_covers_all": self._evaluate_release_plan_covers_all,
        }
        handler = handlers.get(action)
        if handler is None:
            return EnvironmentFeedback(
                success=False, score=0.0,
                details=[f"Unknown action type '{action}'. Known: {sorted(handlers)}"],
            )

        conn = db.get_connection()
        try:
            return handler(conn, payload)
        except KeyError as exc:
            return EnvironmentFeedback(
                success=False, score=0.0,
                details=[f"state is missing required field {exc}."],
            )
        finally:
            conn.close()

    # ---- grounded checks, one per action type --------------------------

    def _evaluate_deploy_pr(self, conn, payload: dict) -> EnvironmentFeedback:
        repository_name = payload["repository_name"]
        environment_name = payload["environment_name"]
        pull_request_id = payload["pull_request_id"]
        details = []

        repository = db.get_repository_by_name(conn, repository_name)
        if repository is None:
            return EnvironmentFeedback(success=False, score=0.0,
                                        details=[f"No repository '{repository_name}'."])

        environment = db.get_environment(conn, repository["id"], environment_name)
        if environment is None:
            return EnvironmentFeedback(
                success=False, score=0.0,
                details=[f"Repository '{repository_name}' has no '{environment_name}' environment."],
            )

        pull_request = db.get_pull_request(conn, pull_request_id)
        if pull_request is None:
            return EnvironmentFeedback(success=False, score=0.0,
                                        details=[f"No pull request #{pull_request_id}."])
        if pull_request["repository_id"] != repository["id"]:
            return EnvironmentFeedback(
                success=False, score=0.0,
                details=[f"PR #{pull_request_id} belongs to a different repository."],
            )

        if pull_request["status"] not in ("Approved", "Merged"):
            details.append(
                f"PR #{pull_request_id} status is '{pull_request['status']}', "
                f"not Approved/Merged."
            )

        scan = db.get_latest_security_scan(conn, pull_request_id)
        scan_status = scan["status"] if scan else "Pending"
        if environment_name == "production" and scan_status != "Passed":
            details.append(f"Latest security scan is '{scan_status}', not Passed.")

        in_flight = db.get_in_flight_deployment(conn, repository["id"], environment["id"])
        if in_flight is not None:
            details.append(
                f"Deployment #{in_flight['id']} is already {in_flight['status']} "
                f"for this repository/environment."
            )

        open_incident = conn.execute(
            """
            SELECT i.id, i.title, i.severity
            FROM incidents i
            JOIN deployments d ON d.id = i.deployment_id
            WHERE d.repository_id = ? AND i.status = 'open'
              AND i.severity IN ('high', 'critical')
            ORDER BY i.severity DESC LIMIT 1
            """,
            (repository["id"],),
        ).fetchone()
        if open_incident is not None:
            details.append(
                f"Repository '{repository_name}' has an open {open_incident['severity']} "
                f"incident (#{open_incident['id']}: '{open_incident['title']}') — deploying "
                f"more changes on top of it is unsafe without an explicit override."
            )

        success = not details
        score = 1.0 if success else max(0.0, 1.0 - 0.25 * len(details))
        return EnvironmentFeedback(success=success, score=round(score, 4), details=details)

    def _evaluate_merge_pr(self, conn, payload: dict) -> EnvironmentFeedback:
        pull_request_id = payload["pull_request_id"]
        details = []

        pull_request = db.get_pull_request(conn, pull_request_id)
        if pull_request is None:
            return EnvironmentFeedback(success=False, score=0.0,
                                        details=[f"No pull request #{pull_request_id}."])

        if pull_request["status"] != "Approved":
            details.append(f"PR #{pull_request_id} is '{pull_request['status']}', not Approved.")

        scan = db.get_latest_security_scan(conn, pull_request_id)
        if scan is None or scan["status"] != "Passed":
            details.append(
                f"Latest security scan is "
                f"'{scan['status'] if scan else 'missing'}', not Passed."
            )

        success = not details
        score = 1.0 if success else max(0.0, 1.0 - 0.5 * len(details))
        return EnvironmentFeedback(success=success, score=round(score, 4), details=details)

    def _evaluate_rollback_deployment(self, conn, payload: dict) -> EnvironmentFeedback:
        deployment_id = payload["deployment_id"]
        row = conn.execute(
            "SELECT id, status FROM deployments WHERE id = ?", (deployment_id,)
        ).fetchone()
        if row is None:
            return EnvironmentFeedback(success=False, score=0.0,
                                        details=[f"No deployment #{deployment_id}."])
        if row["status"] not in ("Succeeded", "InProgress"):
            return EnvironmentFeedback(
                success=False, score=0.0,
                details=[f"Deployment #{deployment_id} is '{row['status']}'; only a "
                         f"Succeeded or InProgress deployment can be rolled back."],
            )
        return EnvironmentFeedback(success=True, score=1.0, details=[])

    def _evaluate_release_plan_covers_all(self, conn, payload: dict) -> EnvironmentFeedback:
        repository_name = payload["repository_name"]
        candidate_ids = payload["candidate_pull_request_ids"]
        draft = payload.get("draft", "")
        details = []

        repository = db.get_repository_by_name(conn, repository_name)
        if repository is None:
            return EnvironmentFeedback(success=False, score=0.0,
                                        details=[f"No repository '{repository_name}'."])

        missing = [pid for pid in candidate_ids
                   if f"#{pid}" not in draft and f"PR {pid}" not in draft]
        if missing:
            details.append(f"Draft never mentions PR(s): {missing}.")

        blocked_ids = []
        for pid in candidate_ids:
            pr = db.get_pull_request(conn, pid)
            scan = db.get_latest_security_scan(conn, pid)
            scan_status = scan["status"] if scan else "Pending"
            if pr is None:
                continue
            if pr["status"] not in ("Approved", "Merged") or scan_status != "Passed":
                blocked_ids.append(pid)
        for pid in blocked_ids:
            marker_present = any(
                word in draft.upper() for word in ("BLOCK", "HOLD", "NOT READY", "DO NOT")
            ) and (f"#{pid}" in draft or f"PR {pid}" in draft)
            if not marker_present:
                details.append(
                    f"PR #{pid} is not release-ready (scan/status not clean) but the "
                    f"draft doesn't clearly flag it as blocked."
                )

        open_incident = conn.execute(
            """
            SELECT i.id FROM incidents i
            JOIN deployments d ON d.id = i.deployment_id
            WHERE d.repository_id = ? AND i.status = 'open'
              AND i.severity IN ('high', 'critical')
            """,
            (repository["id"],),
        ).fetchone()
        if open_incident is not None and not any(
            word in draft.upper() for word in ("HOLD", "INCIDENT", "PAUSE")
        ):
            details.append(
                f"Repository '{repository_name}' has an open high/critical incident but "
                f"the draft never mentions it."
            )

        success = not details
        score = 1.0 if success else max(0.0, 1.0 - 0.2 * len(details))
        return EnvironmentFeedback(success=success, score=round(score, 4), details=details)