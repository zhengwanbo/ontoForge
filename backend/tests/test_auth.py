import asyncio
import unittest

from fastapi import HTTPException

from app.core.auth import normalize_role, require_admin


class AdminRoleCompatibilityTests(unittest.TestCase):
    def test_legacy_uppercase_admin_role_is_accepted(self):
        result = asyncio.run(require_admin({"role": "ADMIN"}))
        self.assertEqual(result["role"], "ADMIN")

    def test_role_is_normalized_for_login_tokens(self):
        self.assertEqual(normalize_role(" Admin "), "admin")

    def test_non_admin_role_is_rejected(self):
        with self.assertRaises(HTTPException) as context:
            asyncio.run(require_admin({"role": "analyst"}))
        self.assertEqual(context.exception.status_code, 403)
