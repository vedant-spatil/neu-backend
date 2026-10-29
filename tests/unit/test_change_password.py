from app.api.routers.auth import password_change_error


def test_a_wrong_current_password_is_refused():
    assert password_change_error(False, "old-password-1", "new-password-12") == "the current password is wrong"


def test_a_short_new_password_is_refused():
    assert password_change_error(True, "old-password-1", "short") == "use at least 10 characters for the new password"


def test_the_same_password_is_refused():
    assert password_change_error(True, "same-password-1", "same-password-1") == \
        "the new password must differ from the current one"


def test_a_valid_change_has_no_problem():
    assert password_change_error(True, "old-password-1", "new-password-12") is None
