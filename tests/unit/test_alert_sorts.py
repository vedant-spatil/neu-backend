from app.api.routers.cases import ALERT_SORTS


def test_sort_options_match_the_endpoint_pattern():
    assert set(ALERT_SORTS) == {"score_desc", "score_asc", "ts_desc", "ts_asc", "amount_desc"}


def test_every_sort_breaks_ties_by_id():
    for order in ALERT_SORTS.values():
        assert len(order) == 2  # the primary sort, then the id tie-break
