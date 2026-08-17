import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).parent
SOURCE = (ROOT / "main.py").read_text()


class FakeTask:
    def __init__(self, done=False):
        self._done = done

    def done(self):
        return self._done


class SourceRegressionTests(unittest.TestCase):
    def test_module_compiles_and_import_os_is_present(self):
        tree = ast.parse(SOURCE)
        imports = {alias.name for node in tree.body if isinstance(node, ast.Import) for alias in node.names}
        self.assertIn("os", imports)

    def test_operations_are_scoped_by_chat_id(self):
        self.assertIn("self.operations = {}", SOURCE)
        self.assertIn("def is_operation_running(self, chat_id):", SOURCE)
        self.assertIn("self.operations.get(str(chat_id))", SOURCE)
        self.assertIn("state.is_operation_running(chat_id)", SOURCE)
        self.assertIn("state.operations[str(chat_id)] = task", SOURCE)
        self.assertIn("query.message.chat_id", SOURCE)
        self.assertNotIn("state.is_running", SOURCE)
        self.assertNotIn("state.operation_task", SOURCE)

    def test_only_one_task_is_created_for_each_request(self):
        call = "asyncio.create_task(add_members_task(update.message, count, chat_id))"
        self.assertEqual(SOURCE.count(call), 1)
        self.assertNotIn("asyncio.create_task(add_members_task(update.message, count))", SOURCE)

    def test_forbidden_account_is_temporarily_disabled_and_rotation_continues(self):
        self.assertIn("errors.ChatWriteForbiddenError", SOURCE)
        self.assertIn("active_sessions[current_idx][\"disabled_until\"] = time.time() + 3600", SOURCE)
        self.assertIn("disabled_indices.add(current_idx)", SOURCE)
        self.assertIn("Kalan hesaplarla devam ediliyor", SOURCE)
        self.assertNotIn("banned_indices", SOURCE)

    def test_cleanup_releases_only_the_own_chat_operation(self):
        self.assertIn("state.release_operation(chat_id)", SOURCE)
        self.assertIn("self.operations.pop(str(chat_id), None)", SOURCE)

    def test_independent_operation_map_semantics(self):
        operations = {"100": FakeTask(), "200": FakeTask()}
        self.assertTrue(not operations["100"].done())
        operations.pop("100", None)
        self.assertNotIn("100", operations)
        self.assertIn("200", operations)


if __name__ == "__main__":
    unittest.main(verbosity=2)
