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


def test_forgot_password_route_not_available(client):
    resp = client.get("/forgot-password", follow_redirects=False)
    assert resp.status_code == 404
