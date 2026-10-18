"""Guards against runaway agent loops that burn tokens (see RECURSION_LIMIT in services/cases.py)."""

from app.orchestration.graph import build_graph
from app.services.cases import RECURSION_LIMIT


def _has_cycle(edges: set[tuple[str, str]]) -> bool:
    adj: dict[str, list[str]] = {}
    for a, b in edges:
        adj.setdefault(a, []).append(b)
    state: dict[str, int] = {}  # 0 unseen, 1 on stack, 2 done

    def visit(n: str) -> bool:
        state[n] = 1
        for m in adj.get(n, []):
            if state.get(m) == 1:
                return True
            if state.get(m) is None and visit(m):
                return True
        state[n] = 2
        return False

    return any(state.get(n) is None and visit(n) for n in list(adj))


def test_workflow_graph_is_acyclic():
    g = build_graph(checkpointer=None).get_graph()
    edges = {(e.source, e.target) for e in g.edges}
    assert not _has_cycle(edges)


def test_recursion_limit_is_bounded():
    assert 0 < RECURSION_LIMIT <= 25


def test_llm_retries_are_bounded():
    import inspect

    from app.services import llm

    src = inspect.getsource(llm.complete_json)
    assert "range(3)" in src  # at most 3 provider calls per request
