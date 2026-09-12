import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.api.system import _build_system_info, _sanitize_database_url
from app.models.models import SysDataSource, SysDomain, SysLLMConfig, SysOperationLog, SysUser


class _FakeQuery:
    def __init__(self, total_count: int, filtered_count: int | None = None):
        self.total_count = total_count
        self.filtered_count = filtered_count if filtered_count is not None else total_count
        self.filtered = False

    def filter(self, *_args, **_kwargs):
        self.filtered = True
        return self

    def count(self):
        return self.filtered_count if self.filtered else self.total_count


class _FakeSession:
    def __init__(self):
        self.queries = {
            SysUser: _FakeQuery(total_count=5, filtered_count=4),
            SysDomain: _FakeQuery(total_count=3),
            SysLLMConfig: _FakeQuery(total_count=6, filtered_count=2),
            SysDataSource: _FakeQuery(total_count=7),
            SysOperationLog: _FakeQuery(total_count=28),
        }

    def query(self, model):
        return self.queries[model]


class SystemInfoTest(unittest.TestCase):
    def test_sanitize_database_url_masks_password_and_extracts_parts(self) -> None:
        result = _sanitize_database_url("oracle+oracledb://system:oracle@localhost:1521/FREEPDB1")

        self.assertEqual("oracle", result["dialect"])
        self.assertEqual("oracledb", result["driver"])
        self.assertEqual("localhost", result["host"])
        self.assertEqual("FREEPDB1", result["database"])
        self.assertEqual("oracle+oracledb://system:***@localhost:1521/FREEPDB1", result["masked_url"])

    def test_build_system_info_returns_git_runtime_and_resource_counts(self) -> None:
        db = _FakeSession()
        fake_git = {
            "branch": "main",
            "commit_hash": "abcdef1234567890",
            "short_commit_hash": "abcdef1",
            "commit_subject": "Add system info endpoint",
            "commit_time": "2026-09-11T01:23:45+00:00",
            "tag": "v1.4.1",
            "nearest_tag": "v1.4.1",
            "worktree_dirty": False,
        }

        with patch("app.api.system._load_git_metadata", return_value=fake_git), patch(
            "app.api.system.settings",
            new=SimpleNamespace(
                APP_NAME="本体构建平台",
                APP_VERSION="1.4.1",
                API_PREFIX="/api/v1",
                DEBUG=True,
                LOG_LEVEL="INFO",
                SQL_ECHO=False,
                DATABASE_URL="oracle+oracledb://system:oracle@localhost:1521/FREEPDB1",
            ),
        ):
            result = _build_system_info(db)

        self.assertEqual("1.4.1", result["application"]["version"])
        self.assertEqual("main", result["git"]["branch"])
        self.assertEqual("v1.4.1", result["git"]["tag"])
        self.assertEqual("oracle", result["database"]["dialect"])
        self.assertEqual("oracle+oracledb://system:***@localhost:1521/FREEPDB1", result["database"]["masked_url"])
        self.assertEqual(5, result["resources"]["user_count"])
        self.assertEqual(4, result["resources"]["active_user_count"])
        self.assertEqual(3, result["resources"]["domain_count"])
        self.assertEqual(6, result["resources"]["llm_config_count"])
        self.assertEqual(2, result["resources"]["active_llm_config_count"])
        self.assertEqual(7, result["resources"]["data_source_count"])
        self.assertEqual(28, result["resources"]["operation_log_count"])
        self.assertIn("python_version", result["runtime"])
        self.assertIsInstance(result["application"]["uptime_seconds"], int)


if __name__ == "__main__":
    unittest.main()
