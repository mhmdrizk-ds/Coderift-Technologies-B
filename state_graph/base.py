from __future__ import annotations

from typing import Any, Callable, Optional

from state_graph.contracts import Interrupt, NodeFailure
from state_graph.store import CheckpointStore, HitlStore, TicketStore

NodeFn = Callable[[dict], Optional[dict]]
ConditionFn = Callable[[dict], str]
WAIT_KEY = "__wait__"


class StateGraph:
    def __init__(self, name: str,
                  checkpointer: Optional[CheckpointStore] = None,
                  hitl_store: Optional[HitlStore] = None,
                  ticket_store: Optional[TicketStore] = None):
        self.name = name
        self.nodes: dict[str, NodeFn] = {}
        self._edges: dict[str, str] = {}
        self._conditional_edges: dict[str, tuple[ConditionFn, dict[str, Optional[str]]]] = {}
        self.entry_point: Optional[str] = None

        self.checkpointer = checkpointer or CheckpointStore()
        self.hitl_store = hitl_store or HitlStore()
        self.ticket_store = ticket_store or TicketStore()

    #  graph construction 

    def add_node(self, name: str, fn: NodeFn) -> "StateGraph":
        self.nodes[name] = fn
        return self

    def set_entry_point(self, name: str) -> "StateGraph":
        self.entry_point = name
        return self

    def add_edge(self, from_node: str, to_node: Optional[str]) -> "StateGraph":
        self._edges[from_node] = to_node
        return self

    def add_conditional_edges(self, from_node: str, condition_fn: ConditionFn,
                                mapping: dict[str, Optional[str]]) -> "StateGraph":
        self._conditional_edges[from_node] = (condition_fn, mapping)
        return self

    def _next_node(self, current: str, state: dict) -> Optional[str]:
        if current in self._conditional_edges:
            condition_fn, mapping = self._conditional_edges[current]
            branch_key = condition_fn(state)
            if branch_key not in mapping:
                raise ValueError(
                    f"node '{current}': condition returned '{branch_key}', "
                    f"which is not in the mapping {list(mapping)}"
                )
            return mapping[branch_key]
        return self._edges.get(current)

    # -- execution ---------------------------------------------------------

    def start(self, run_id: str, initial_state: dict) -> dict:
        if self.entry_point is None:
            raise ValueError(f"graph '{self.name}' has no entry point set")
        state = dict(initial_state)
        self.checkpointer.save(run_id, self.name, self.entry_point, state,
                                 status="running")
        return self.resume(run_id)

    def resume(self, run_id: str, hitl_decision: Optional[dict] = None,
                external_event: Optional[dict] = None) -> dict:
        checkpoint = self.checkpointer.load_latest(run_id)
        if checkpoint is None:
            raise ValueError(f"no checkpoint found for run_id={run_id!r}")

        current_node = checkpoint.node_name
        state = dict(checkpoint.state)

        if hitl_decision is not None:
            state["_hitl_decision"] = hitl_decision
        if external_event:
            state.update(external_event)

        while current_node is not None:
            node_fn = self.nodes.get(current_node)
            if node_fn is None:
                raise ValueError(f"graph '{self.name}' has no node "
                                    f"'{current_node}'")

            try:
                result = node_fn(state)
            except Interrupt as intr:
                self.checkpointer.save(run_id, self.name, current_node,
                                         state, status="paused_hitl")
                self.hitl_store.create(run_id=run_id, graph_name=self.name,
                                         node_name=current_node,
                                         reason=intr.reason, payload=intr.payload)
                return {"status": "paused_hitl", "node": current_node,
                        "run_id": run_id}
            except NodeFailure as fail:
                self.checkpointer.save(run_id, self.name, current_node,
                                         state, status="ticketed")
                self.ticket_store.create(run_id=run_id, graph_name=self.name,
                                           node_name=current_node,
                                           error_code=fail.error_code,
                                           message=fail.message,
                                           payload=fail.payload,
                                           state_snapshot=state)
                return {"status": "ticketed", "node": current_node,
                        "run_id": run_id}

            if isinstance(result, dict) and result.get(WAIT_KEY):
                merged = {**state, **{k: v for k, v in result.items() if k != WAIT_KEY}}
                self.checkpointer.save(run_id, self.name, current_node,
                                         merged, status="waiting")
                return {"status": "waiting", "node": current_node,
                        "run_id": run_id}

            state = {**state, **(result or {})}
            state.pop("_hitl_decision", None)  
            next_node = self._next_node(current_node, state)
            self.checkpointer.save(
                run_id, self.name, next_node or current_node, state,
                status="completed" if next_node is None else "running",
            )
            current_node = next_node

        return {"status": "completed", "state": state, "run_id": run_id}