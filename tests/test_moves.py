"""Tests for lmp.core.moves."""

import unittest
from unittest import mock

from lmp.core import moves, relocate
from lmp.core.apps import AppEntry
from helpers import HOME


class MovesTests(unittest.TestCase):
    def test_ollama_data_is_found_and_status_follows_moves(self):
        models = HOME / ".ollama/models"
        models.mkdir(parents=True, exist_ok=True)
        (models / "blob").write_text("x" * 100)
        app = AppEntry(ident="ollama", name="Ollama", source="repo")
        self.assertIn(str(HOME / ".ollama"), moves.data_paths(app))
        state = moves.MoveState()
        with mock.patch.object(relocate, "candidate_dirs", return_value=[]), \
                mock.patch("lmp.core.pacman.package_files", return_value=[]):
            plan = moves.plan_app(app, state)
        self.assertEqual([p.kind for p in plan.movable], [moves.DATA])
        self.assertTrue(plan.note)        # explains that only the data can move
        self.assertEqual(state.app_status(app)[0], moves.SYSTEM)
        state.user_moves.append(relocate.UserRelocation(str(HOME / ".ollama"), "/nope/x",
                                                        "/nope", 100, "now"))
        self.assertEqual(state.app_status(app)[0], moves.DISCONNECTED)

    def test_flatpak_parts(self):
        app = AppEntry(ident="org.x.App", name="X", source="flatpak", size=10,
                       installation="system")
        plan = moves.plan_app(app, moves.MoveState())
        self.assertEqual(plan.parts[0].kind, moves.FLATPAK)
        self.assertFalse(plan.parts[0].moved)
