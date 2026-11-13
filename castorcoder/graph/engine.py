"""Deterministic wave-based graph engine for structured agent pipelines.

Adapted from waku-agent's graph engine:
- Nodes: Discrete executable steps that take shared state and return keys to merge.
- Edges: Unconditional transitions.
- Routers: Pure Python functions over state dict that determine next steps.
  "Models write state; code reads it and picks the edge. No LLM ever decides
  control flow directly."
- Wave Execution: Ready nodes run concurrently (via ThreadPoolExecutor). Parallel
  nodes must write disjoint keys; collisions raise GraphStateCollision early.
- Guards: Per-node max_visits (bounded cycles) + global max_steps limit.
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

START = "START"
END = "END"

NodeFn = Callable[[dict[str, Any]], dict[str, Any]]
RouteFn = Callable[[dict[str, Any]], str]
Observer = Callable[[str, dict[str, Any]], None]


class GraphStateCollision(Exception):
    """Two nodes executed in the same wave attempted to write to the same state key."""


class GraphCycleLimit(Exception):
    """A node exceeded its maximum allowed visits (loop guard triggered)."""


@dataclass
class Node:
    name: str
    fn: NodeFn
    kind: str = "fn"  # 'fn', 'llm', 'tool', 'agent', 'eval'
    max_visits: int = 1  # Allowed cycles for iterative refinement
    on_error: str | None = None  # Node to jump to on exception (default: route to END)


@dataclass
class Graph:
    name: str
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[tuple[str, str]] = field(default_factory=list)
    routers: dict[str, tuple[RouteFn, dict[str, str]]] = field(default_factory=dict)

    def add_node(self, node: Node) -> None:
        if node.name in (START, END):
            raise ValueError(f"Node name '{node.name}' is reserved.")
        self.nodes[node.name] = node

    def add_edge(self, src: str, dst: str) -> None:
        """Unconditional edge from src to dst."""
        for end in (src, dst):
            if end not in self.nodes and end not in (START, END):
                raise ValueError(f"Unknown graph endpoint '{end}'")
        self.edges.append((src, dst))

    def add_router(self, src: str, route: RouteFn, targets: dict[str, str]) -> None:
        """Conditional code router after src node finishes."""
        if src not in self.nodes:
            raise ValueError(f"Source node '{src}' not registered in graph.")
        for label, dst in targets.items():
            if dst not in self.nodes and dst != END:
                raise ValueError(f"Router target '{dst}' for label '{label}' is unknown.")
        self.routers[src] = (route, targets)

    def describe(self) -> dict[str, Any]:
        """Returns the graph topology data for tracing or visualization."""
        edges = [{"src": s, "dst": d, "conditional": False} for s, d in self.edges]
        for src, (_route, targets) in self.routers.items():
            for label, dst in targets.items():
                edges.append({"src": src, "dst": dst, "conditional": True, "label": label})
        return {
            "name": self.name,
            "nodes": [
                {"name": n.name, "kind": n.kind, "max_visits": n.max_visits}
                for n in self.nodes.values()
            ],
            "edges": edges,
        }

    def run(
        self,
        initial_state: dict[str, Any] | None = None,
        *,
        observer: Observer | None = None,
        max_steps: int = 50,
        max_workers: int = 4,
    ) -> dict[str, Any]:
        """Execute the graph wave-by-wave until END is reached or no ready nodes remain."""
        state: dict[str, Any] = dict(initial_state or {})
        visits: dict[str, int] = defaultdict(int)

        def emit(kind: str, data: dict[str, Any]) -> None:
            if observer:
                observer(kind, data)

        emit("graph_start", {"graph": self.name, "initial_state": dict(state)})

        # Find initial ready nodes connected from START
        ready: list[str] = [dst for src, dst in self.edges if src == START]
        # Fallback: if no explicit START edge, take first added node
        if not ready and self.nodes:
            ready = [next(iter(self.nodes.keys()))]

        steps = 0
        while ready and steps < max_steps:
            steps += 1
            current_wave = list(dict.fromkeys(ready))  # deduplicate preserving order
            ready = []

            # Check visit limits before executing
            for name in current_wave:
                if name == END:
                    continue
                node = self.nodes.get(name)
                if not node:
                    continue
                if visits[name] >= node.max_visits:
                    emit("node_guard_exceeded", {"node": name, "visits": visits[name]})
                    raise GraphCycleLimit(
                        f"Node '{name}' exceeded max_visits ({node.max_visits})."
                    )

            # Filter non-END nodes to run
            executable = [self.nodes[name] for name in current_wave if name in self.nodes and name != END]
            if not executable:
                break

            # Execute wave in parallel
            def run_one(n: Node) -> tuple[Node, dict[str, Any] | None, Exception | None]:
                visits[n.name] += 1
                t0 = time.perf_counter()
                emit("node_start", {"node": n.name, "kind": n.kind, "visit": visits[n.name]})
                try:
                    res = n.fn(dict(state))
                    dur = round((time.perf_counter() - t0) * 1000, 2)
                    emit("node_end", {"node": n.name, "duration_ms": dur, "updates": list(res.keys())})
                    return (n, res, None)
                except Exception as exc:
                    dur = round((time.perf_counter() - t0) * 1000, 2)
                    emit("node_error", {"node": n.name, "duration_ms": dur, "error": str(exc)})
                    return (n, None, exc)

            with ThreadPoolExecutor(max_workers=min(max_workers, len(executable))) as pool:
                results = list(pool.map(run_one, executable))

            # Merge results into state with collision detection
            seen_keys: dict[str, str] = {}
            next_candidates: list[str] = []

            for node, updates, error in results:
                if error:
                    if node.on_error:
                        next_candidates.append(node.on_error)
                    else:
                        state["_last_error"] = f"Node '{node.name}' failed: {error}"
                        next_candidates.append(END)
                    continue

                if updates:
                    for k in updates:
                        if k in seen_keys:
                            raise GraphStateCollision(
                                f"Nodes '{seen_keys[k]}' and '{node.name}' both wrote key '{k}' "
                                f"in the same wave."
                            )
                        seen_keys[k] = node.name
                    state.update(updates)

                # Determine next nodes: Router vs Unconditional edges
                if node.name in self.routers:
                    route_fn, targets = self.routers[node.name]
                    target_label = route_fn(dict(state))
                    target_node = targets.get(target_label, END)
                    emit("route", {"src": node.name, "label": target_label, "dst": target_node})
                    next_candidates.append(target_node)
                else:
                    for s, d in self.edges:
                        if s == node.name:
                            next_candidates.append(d)

            # Check if END is in the candidates
            if END in next_candidates:
                break
            ready = [c for c in next_candidates if c != END]

        emit("graph_end", {"graph": self.name, "steps": steps, "final_state": dict(state)})
        return state
