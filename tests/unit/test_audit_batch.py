from app.services.audit import write_events


class FakeSession:
    def __init__(self) -> None:
        self.added: list = []
        self.commits = 0

    def add_all(self, rows) -> None:
        self.added.extend(rows)

    def commit(self) -> None:
        self.commits += 1


def test_write_events_adds_every_row_and_commits_once():
    db = FakeSession()
    write_events(db, [(1, "projects", "added_to_project", {"account_id": "A1"}),
                      (2, "projects", "added_to_project", None)])
    assert [(e.case_id, e.node, e.kind) for e in db.added] == [
        (1, "projects", "added_to_project"),
        (2, "projects", "added_to_project"),
    ]
    assert db.added[1].payload_json == {}  # a missing payload is stored as an empty object
    assert db.commits == 1
