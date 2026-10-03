"""Agents write a spoken version in the same Claude call (voice mode only)."""
import os
import types
import unittest

from helpers import AGENTS, fake_message, install_stubs, isolated_modules, load_file


class WillDueVoice(unittest.TestCase):
    def test_voice_line_split_and_commands_still_run(self):
        install_stubs()
        os.environ.setdefault("ANTHROPIC_API_KEY", "test")
        added = []
        sheets = types.ModuleType("sheets")
        sheets.get_tasks = lambda: [{"ID": 1, "Task": "Call dentist", "Owner": "Gio", "Priority": "High",
                                     "Priority Score": 1, "Status": "Pending"}]
        sheets.add_task = lambda *a: added.append(a)
        sheets.complete_tasks_by_id = sheets.delete_task_by_id = sheets.reopen_task_by_id = lambda *a: None
        sheets.get_filtered_tasks = lambda **k: []
        with isolated_modules({"sheets": sheets}, path=os.path.join(AGENTS, "will_due")):
            agent = load_file(os.path.join(AGENTS, "will_due", "agent.py"), "will_due_agent_under_test")
        seen = {}

        def create(**k):
            seen["msg"] = k["messages"][-1]["content"]
            return fake_message("Added *Book flights*! ✅\nADD_TASK|Book flights|Gio|This Week|2|Gio\n"
                                "VOICE|Done, I added booking flights to your list.")
        agent.client = types.SimpleNamespace(messages=types.SimpleNamespace(create=create))

        r = agent.process_message("add book flights this week", "Gio", "1", voice_mode=True)
        self.assertIn("VOICE|", seen["msg"])
        self.assertEqual(r["response_text"], "Added *Book flights*! ✅")
        self.assertEqual(r["voice_text"], "Done, I added booking flights to your list.")
        self.assertEqual(added, [("Book flights", "Gio", "This Week", "2", "Gio")])

        agent.process_message("add book flights this week", "Gio", "1", voice_mode=False)
        self.assertNotIn("VOICE|", seen["msg"], "Telegram requests don't ask for a voice line")


if __name__ == "__main__":
    unittest.main()
