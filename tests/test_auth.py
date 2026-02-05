from barangay_project.models import PasswordReset


def test_login_logout(client, make_user):
    user = make_user("clerk", "Clerk123!")

    resp = client.post(
        "/login",
        data={"username": user.username, "password": "Clerk123!"},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    with client.session_transaction() as sess:
        assert sess.get("_user_id") == str(user.id)

    resp = client.get("/logout", follow_redirects=False)
    assert resp.status_code == 302

    with client.session_transaction() as sess:
        assert sess.get("_user_id") is None


def test_login_rate_limit(client, app, make_user):
    make_user("clerk", "Correct123!")
    max_attempts = int(app.config.get("LOGIN_RATE_LIMIT_MAX", 3))

    for _ in range(max_attempts):
        client.post(
            "/login",
            data={"username": "clerk", "password": "WrongPass123!"},
            follow_redirects=False,
        )

    resp = client.post(
        "/login",
        data={"username": "clerk", "password": "WrongPass123!"},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert b"Too many failed login attempts" in resp.data


def test_password_reset_flow(client, make_user):
    user = make_user("clerk", "OldPass123!", email="clerk@example.com")

    resp = client.post(
        "/forgot-password",
        data={"username": user.username},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    reset = PasswordReset.query.filter_by(user_id=user.id).first()
    assert reset is not None

    new_password = "NewPass123!"
    resp = client.post(
        "/reset-password",
        data={
            "username": user.username,
            "otp_code": reset.otp_code,
            "new_password": new_password,
            "confirm_new_password": new_password,
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302

    resp = client.post(
        "/login",
        data={"username": user.username, "password": new_password},
        follow_redirects=False,
    )
    assert resp.status_code == 302
