"""LangGraph workflow: 10 stages, human-in-the-loop approval via interrupt()/Command(resume=...).

LangGraph APIs used (all documented, public): StateGraph, START, END, add_node, add_edge,
add_conditional_edges, compile(checkpointer=...), graph.invoke, graph.get_state, langgraph.types.interrupt,
langgraph.types.Command, MemorySaver / SqliteSaver checkpointers.
"""
from __future__ import annotations
import operator, sqlite3
from typing import Annotated, TypedDict
from .nodes import Nodes, STAGES, next_stage

class WorkflowState(TypedDict, total=False):
    status: str
    stage: str
    error: str
    overall_percent: float
    pending: list
    approval_rounds: int
    approved_by: str
    map_hash: str
    migration_status: str
    verification_status: str
    force_new_map: bool
    ai_notes: Annotated[list, operator.add]
    log: Annotated[list, operator.add]

def get_checkpointer(settings):
    """MemorySaver by default (state lives for this process). If CHECKPOINT_DB is set and
    langgraph-checkpoint-sqlite is installed, state persists in SQLite so an interrupted approval can be
    resumed from a later process using the same thread id."""
    if settings.checkpoint_db:
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
            return SqliteSaver(sqlite3.connect(settings.checkpoint_db, check_same_thread=False))
        except ImportError:
            pass
    from langgraph.checkpoint.memory import MemorySaver
    return MemorySaver()

def build_graph(pipeline, ai, checkpointer=None):
    from langgraph.graph import StateGraph, START, END
    from langgraph.types import interrupt
    nodes = Nodes(pipeline, ai)

    def human_approval(state: WorkflowState):
        payload = nodes.approval_payload(state)
        resume = interrupt(payload)           # pauses here; value comes from Command(resume=...)
        return nodes.apply_approval(state, resume)

    g = StateGraph(WorkflowState)
    for name in STAGES:
        g.add_node(name, human_approval if name == "human_approval" else getattr(nodes, name))
    g.add_edge(START, STAGES[0])
    for name in STAGES:
        dests = {d: d for d in set(STAGES) | {"END"} if d != "END"}
        dests["END"] = END
        g.add_conditional_edges(name, lambda s, _n=name: next_stage(_n, s), dests)
    return g.compile(checkpointer=checkpointer)

def run_workflow(pipeline, ai, approver, thread_id: str = "schemashift-demo", checkpointer=None, initial=None):
    """Run to completion. `approver(payload) -> resume dict` is called at each approval interrupt.
    If the thread already has a pending interrupt (persistent checkpointer) it is resumed, not restarted."""
    from langgraph.types import Command
    graph = build_graph(pipeline, ai, checkpointer or get_checkpointer(pipeline.st))
    cfg = {"configurable": {"thread_id": thread_id}}
    if not graph.get_state(cfg).next:
        graph.invoke({"status": "RUNNING", "log": [], "ai_notes": [], **(initial or {})}, cfg)
    while True:
        snap = graph.get_state(cfg)
        if not snap.next:
            return snap.values
        payload = next((i.value for t in snap.tasks for i in t.interrupts), None)
        if payload is None:
            raise RuntimeError(f"workflow paused at {snap.next} without an approval payload")
        graph.invoke(Command(resume=approver(payload)), cfg)
