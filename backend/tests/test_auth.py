"""Shared-password protection: open without a password, enforced with one, CORS-readable 401s."""
from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from unittest import mock

from fastapi.testclient import TestClient

from app import auth, main

ORIGIN = main.CORS_ORIGINS[0]


@asynccontextmanager
async def _no_lifespan(_app):
    yield


class AuthTests(unittest.TestCase):
    def setUp(self):
        self._lifespan = main.app.router.lifespan_context
        main.app.router.lifespan_context = _no_lifespan
        auth._failures.clear()

    def tearDown(self):
        main.app.router.lifespan_context = self._lifespan
        auth._failures.clear()

    def client(self, password: str = "") -> TestClient:
        patcher = mock.patch.object(auth, "ACCESS_PASSWORD", password)
        patcher.start()
        self.addCleanup(patcher.stop)
        return TestClient(main.app)

    def test_without_password_everything_stays_open(self):
        c = self.client("")
        self.assertEqual(c.get("/api/documents").status_code, 200)
        self.assertEqual(c.get("/api/auth/status").json(), {"required": False, "signed_in": True})

    def test_password_required_for_api_but_not_health(self):
        c = self.client("s3cret")
        self.assertEqual(c.get("/api/health").status_code, 200)
        self.assertEqual(c.get("/api/auth/status").json(), {"required": True, "signed_in": False})
        for path in ("/api/documents", "/api/extraction/projects", "/api/documents/x/file"):
            self.assertEqual(c.get(path).status_code, 401, path)

    def test_login_then_header_and_query_token_work(self):
        c = self.client("s3cret")
        self.assertEqual(c.post("/api/auth/login", json={"password": "nope"}).status_code, 401)
        token = c.post("/api/auth/login", json={"password": "s3cret"}).json()["token"]
        self.assertEqual(c.get("/api/documents", headers={"Authorization": f"Bearer {token}"}).status_code, 200)
        self.assertEqual(c.get("/api/documents", params={"token": token}).status_code, 200)
        self.assertTrue(c.get("/api/auth/status", headers={"Authorization": f"Bearer {token}"}).json()["signed_in"])

    def test_tampered_expired_and_other_password_tokens_are_rejected(self):
        c = self.client("s3cret")
        token, _ = auth.issue_token()
        payload, signature = token.split(".")
        self.assertFalse(auth.valid_token(payload + "." + signature[::-1]))
        old, _ = auth.issue_token(now=0)
        self.assertFalse(auth.valid_token(old))
        with mock.patch.object(auth, "ACCESS_PASSWORD", "changed"):
            self.assertFalse(auth.valid_token(token))   # changing the password signs everyone out
        self.assertEqual(c.get("/api/documents", params={"token": "garbage"}).status_code, 401)

    def test_repeated_wrong_passwords_are_throttled(self):
        c = self.client("s3cret")
        for _ in range(auth.MAX_FAILURES):
            self.assertEqual(c.post("/api/auth/login", json={"password": "x"}).status_code, 401)
        self.assertEqual(c.post("/api/auth/login", json={"password": "s3cret"}).status_code, 429)

    def test_401_and_preflight_carry_cors_headers(self):
        c = self.client("s3cret")
        denied = c.get("/api/documents", headers={"Origin": ORIGIN})
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(denied.headers.get("access-control-allow-origin"), ORIGIN)
        preflight = c.options("/api/documents", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "GET",
                                                          "Access-Control-Request-Headers": "authorization"})
        self.assertEqual(preflight.status_code, 200)


if __name__ == "__main__":
    unittest.main()
