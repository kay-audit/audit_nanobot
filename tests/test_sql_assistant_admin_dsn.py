from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from workspace.skills.sql_assistant.scripts import _pg_admin as module


class TestAdminDsn(unittest.TestCase):
    def setUp(self):
        self.config = SimpleNamespace(is_settings_initialized=Mock(return_value=False), _initialize_settings=Mock())
        self.db = SimpleNamespace(resolve_dsn=Mock(return_value="postgresql://direct-project-value"))
        self.driver = SimpleNamespace(connect=Mock(return_value=SimpleNamespace(set_session=Mock())))
        self.modules = patch.dict(sys.modules, {"config": self.config, "workspace.utils.db": self.db, "psycopg2": self.driver})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.args = SimpleNamespace(profile="prod", dsn_env=None, dry_run=False)

    def test_project_dsn_delegates_to_shared_resolver_without_env(self):
        with patch.dict(os.environ, {"DATABASE_URL": "wrong-env-value"}):
            module.connect(self.args)
        self.config._initialize_settings.assert_called_once_with("prod")
        self.db.resolve_dsn.assert_called_once()
        self.driver.connect.assert_called_once_with("postgresql://direct-project-value", gssencmode="disable")

    def test_initialized_gateway_settings_are_not_reinitialized(self):
        self.config.is_settings_initialized.return_value = True
        module.resolve_admin_dsn(self.args)
        self.config._initialize_settings.assert_not_called()

    def test_missing_profile_fails_before_connection(self):
        self.args.profile = None
        with self.assertRaisesRegex(ValueError, "--profile"):
            module.connect(self.args)
        self.driver.connect.assert_not_called()

    def test_missing_or_unresolved_dsn_is_rejected(self):
        for dsn in ("", "${DATABASE_URL}", None):
            self.db.resolve_dsn.return_value = dsn
            with self.assertRaisesRegex(ValueError, "project.json"):
                module.connect(self.args)
        self.driver.connect.assert_not_called()

    def test_explicit_legacy_override_is_optional(self):
        self.args.dsn_env = "TEST_SQL_DSN"
        with patch.dict(os.environ, {"TEST_SQL_DSN": "legacy-explicit"}):
            self.assertEqual(module.resolve_admin_dsn(self.args), "legacy-explicit")
        self.db.resolve_dsn.assert_not_called()

    def test_dry_run_connection_is_readonly(self):
        self.args.dry_run = True
        connection = module.connect(self.args)
        connection.set_session.assert_called_once_with(readonly=True)

    def test_connection_error_does_not_expose_dsn(self):
        self.driver.connect.side_effect = RuntimeError("secret DSN password!")
        with self.assertRaisesRegex(RuntimeError, "configuration") as caught:
            module.connect(self.args)
        self.assertNotIn("secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
