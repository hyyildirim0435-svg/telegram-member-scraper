import ast
import asyncio
import os
import unittest
from pathlib import Path

ROOT = Path(__file__).parent
SOURCE = (ROOT / "main.py").read_text()


class SourceRegressionTests(unittest.TestCase):
    def test_module_compiles_and_import_os_is_present(self):
        tree = ast.parse(SOURCE)
        imports = {alias.name for node in tree.body if isinstance(node, ast.Import) for alias in node.names}
        self.assertIn("os", imports)

    def test_operation_is_reserved_before_task_creation(self):
        self.assertIn("state.is_running = True", SOURCE)
        self.assertIn("state.operation_task = task", SOURCE)
        self.assertIn("state.is_running or (state.operation_task and not state.operation_task.done())", SOURCE)
        self.assertLess(SOURCE.index("state.is_running = True"), SOURCE.index("asyncio.create_task(add_members_task"))

    def test_forbidden_account_is_temporarily_disabled_and_rotation_continues(self):
        self.assertIn("errors.ChatWriteForbiddenError", SOURCE)
        self.assertIn("active_sessions[current_idx][\"disabled_until\"] = time.time() + 3600", SOURCE)
        self.assertIn("disabled_indices.add(current_idx)", SOURCE)
        self.assertIn("Kalan hesaplarla devam ediliyor", SOURCE)
        self.assertNotIn("banned_indices", SOURCE)

    def test_cleanup_releases_operation_state(self):
        self.assertIn("finally:", SOURCE)
        self.assertIn("state.operation_task = None", SOURCE)
        self.assertIn("state.is_running = False", SOURCE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
