from app.services import graph_store


def test_templates_are_parameterised():
    for q in (graph_store.FAN_OUT, graph_store.FAN_IN, graph_store.CYCLE):
        assert "$acct" in q
        assert "LIMIT 50" in q
