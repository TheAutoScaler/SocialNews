import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "update_actions.py"
SPEC = importlib.util.spec_from_file_location("update_actions", SCRIPT)
update_actions = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(update_actions)


class UpdateActionsTests(unittest.TestCase):
    def test_updates_sha_but_preserves_reviewed_major_tag(self):
        old = "1" * 40
        new = "2" * 40
        text = f"        uses: actions/checkout@{old} # v7\n"

        updated, changes = update_actions.update_text(text, lambda repo, tag: new)

        self.assertEqual(updated, f"        uses: actions/checkout@{new} # v7\n")
        self.assertEqual(len(changes), 1)

    def test_ignores_unpinned_or_unreviewed_references(self):
        text = "uses: example/action@main\nuses: example/action@" + ("1" * 40) + "\n"
        updated, changes = update_actions.update_text(text, lambda repo, tag: "2" * 40)
        self.assertEqual(updated, text)
        self.assertEqual(changes, [])


if __name__ == "__main__":
    unittest.main()
