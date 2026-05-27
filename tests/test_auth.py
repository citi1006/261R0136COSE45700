from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

import app.main as main_module
from app.auth import hash_password, verify_password
from app.database import clear_owner_accounts, fetch_owner_account, init_db


class AuthTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        init_db()

    def setUp(self) -> None:
        clear_owner_accounts()

    def test_passwords_are_hashed_and_verified(self) -> None:
        password_hash = hash_password("password123")

        self.assertNotIn("password123", password_hash)
        self.assertTrue(verify_password("password123", password_hash))
        self.assertFalse(verify_password("wrong-password", password_hash))

    def test_bootstrap_stores_owner_and_login_returns_token(self) -> None:
        client = TestClient(main_module.app)

        bootstrap = client.post(
            "/api/auth/bootstrap",
            data={"user_id": "store-owner", "password": "password123"},
        )
        self.assertEqual(bootstrap.status_code, 201)
        self.assertIn("access_token", bootstrap.json())

        account = fetch_owner_account("store-owner")
        self.assertIsNotNone(account)
        assert account is not None
        self.assertNotEqual(account["password_hash"], "password123")

        login = client.post(
            "/api/auth/login",
            data={"user_id": "store-owner", "password": "password123"},
        )
        self.assertEqual(login.status_code, 200)
        token = login.json()["access_token"]

        me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["user_id"], "store-owner")

    def test_mobile_policy_requires_authentication(self) -> None:
        client = TestClient(main_module.app)

        unauthenticated = client.get("/api/mobile/upload-policy")
        self.assertEqual(unauthenticated.status_code, 401)

        bootstrap = client.post(
            "/api/auth/bootstrap",
            data={"user_id": "owner", "password": "password123"},
        )
        token = bootstrap.json()["access_token"]
        authenticated = client.get(
            "/api/mobile/upload-policy",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(authenticated.status_code, 200)


if __name__ == "__main__":
    unittest.main()
