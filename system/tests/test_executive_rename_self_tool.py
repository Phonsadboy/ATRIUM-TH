import unittest


class FakeRepo:
    def __init__(self, dept):
        self.dept = dict(dept)
        self.saved_department = None
        self.activities = []

    async def get_department(self, dept_id):
        if dept_id == self.dept["id"]:
            return dict(self.dept)
        return None

    async def save_department(self, dept):
        self.saved_department = dict(dept)
        self.dept = dict(dept)

    async def add_activity(self, activity):
        self.activities.append(activity)


class ExecutiveRenameSelfToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_rename_self_updates_executive_agent_name(self) -> None:
        from app import chat_tools

        active = {"id": "exec", "name": "ผู้บริหาร", "agentName": "ออตโต้"}
        repo = FakeRepo(active)

        result = await chat_tools._rename_self_tool(repo, {"name": "นีโอ"}, active)

        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        self.assertEqual(result["previousName"], "ออตโต้")
        self.assertEqual(result["newName"], "นีโอ")
        self.assertEqual(repo.saved_department["agentName"], "นีโอ")
        self.assertIsInstance(repo.saved_department.get("agentNameUpdatedAt"), int)
        self.assertEqual(active["agentName"], "นีโอ")
        self.assertEqual(active["agentNameUpdatedAt"], repo.saved_department["agentNameUpdatedAt"])
        self.assertIn("นีโอ", repo.activities[0]["text"])

    async def test_rename_self_rejects_non_executive_department(self) -> None:
        from app import chat_tools

        active = {"id": "research", "name": "วิจัย", "agentName": "ไอริส"}
        repo = FakeRepo(active)

        with self.assertRaises(ValueError):
            await chat_tools._rename_self_tool(repo, {"name": "นีโอ"}, active)

    def test_tool_surface_and_keyword_include_rename_self(self) -> None:
        from app import chat_tools

        active = {"id": "exec", "name": "ผู้บริหาร", "agentName": "ออตโต้"}
        tools = chat_tools.chat_tool_definitions([active], active)

        self.assertIn("rename_self", [tool["name"] for tool in tools])
        self.assertTrue(chat_tools.likely_needs_chat_tools("ตั้งชื่อผู้บริหารว่า นีโอ"))

    def test_tool_surface_hides_rename_self_for_departments(self) -> None:
        from app import chat_tools

        exec_dept = {"id": "exec", "name": "ผู้บริหาร", "agentName": "ออตโต้"}
        active = {"id": "research", "name": "วิจัย", "agentName": "ไอริส"}
        tools = chat_tools.chat_tool_definitions([exec_dept, active], active)

        self.assertNotIn("rename_self", [tool["name"] for tool in tools])


if __name__ == "__main__":
    unittest.main()
