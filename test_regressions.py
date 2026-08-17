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

    def test_only_approved_admin_id_is_configured(self):
        self.assertIn("AUTHORIZED_ADMIN_ID = 8302545787", SOURCE)
        self.assertIn("ADMIN_IDS = [AUTHORIZED_ADMIN_ID]", SOURCE)
        self.assertNotIn("8471065820", SOURCE)

    def test_forbidden_account_is_temporarily_disabled_and_rotation_continues(self):
        self.assertIn("errors.ChatWriteForbiddenError", SOURCE)
        self.assertIn("active_sessions[current_idx][\"disabled_until\"] = time.time() + 3600", SOURCE)
        self.assertIn("disabled_indices.add(current_idx)", SOURCE)
        self.assertIn("Kalan hesaplarla devam ediliyor", SOURCE)
        self.assertNotIn("banned_indices", SOURCE)

    def test_persistent_encrypted_session_backup_is_configured(self):
        self.assertIn("SESSION_ENCRYPTION_KEY = os.getenv(\"SESSION_ENCRYPTION_KEY\", \"\")", SOURCE)
        self.assertIn("SESSION_BACKUP_FILE = \"sessions.enc\"", SOURCE)
        self.assertIn("def _session_fernet():", SOURCE)
        self.assertIn("Fernet(SESSION_ENCRYPTION_KEY.encode(\"utf-8\"))", SOURCE)
        self.assertIn("invalid SESSION_ENCRYPTION_KEY", SOURCE)
        self.assertIn("save_encrypted_sessions(self.sessions)", SOURCE)

    def test_account_listing_and_deletion_are_available(self):
        self.assertIn("async def list_accounts_command", SOURCE)
        self.assertIn("callback_data=f\"delete_account:{i}\"", SOURCE)
        self.assertIn("elif data.startswith(\"delete_account:\")", SOURCE)
        self.assertIn("removed = state.sessions.pop(index)", SOURCE)
        self.assertIn("state.save_sessions()", SOURCE)

    def test_message_scan_accepts_only_user_senders(self):
        self.assertIn("sender = msg.sender", SOURCE)
        self.assertIn("isinstance(sender, types.User)", SOURCE)
        self.assertNotIn("msg.sender.bot", SOURCE)

    def test_stop_operation_is_scoped_to_chat_and_reports_count(self):
        self.assertIn("self.stop_events = {}", SOURCE)
        self.assertIn("def request_stop(self, chat_id):", SOURCE)
        self.assertIn("state.stop_events[str(chat_id)] = asyncio.Event()", SOURCE)
        self.assertIn("callback_data=\"stop_add\"", SOURCE)
        self.assertIn("CommandHandler(\"durdur\", stop_add_members)", SOURCE)
        self.assertIn('report[\"stopped\"] = True', SOURCE)
        self.assertIn("Başarıyla Eklenen: {report['added']} kişi", SOURCE)

    def test_cleanup_releases_only_the_own_chat_operation(self):
        self.assertIn("state.release_operation(chat_id)", SOURCE)
        self.assertIn("self.operations.pop(key, None)", SOURCE)

    def test_independent_operation_map_semantics(self):
        operations = {"100": FakeTask(), "200": FakeTask()}
        self.assertTrue(not operations["100"].done())
        operations.pop("100", None)
        self.assertNotIn("100", operations)
        self.assertIn("200", operations)


if __name__ == "__main__":
    unittest.main(verbosity=2)
