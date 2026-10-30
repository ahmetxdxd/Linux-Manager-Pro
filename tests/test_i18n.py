"""Tests for the translation layer and the Turkish catalog."""

import unittest
from pathlib import Path

from lmp import i18n


class I18nTests(unittest.TestCase):
    """Every message id used in the source must have a Turkish translation."""

    def tearDown(self):
        i18n.set_language("en")

    def _used_msgids(self):
        import ast
        root = Path(__file__).resolve().parent.parent / "lmp"
        used = set()
        for path in root.rglob("*.py"):
            if "locales" in path.parts:
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                    continue
                name, args = node.func.id, node.args
                if name in ("tr", "tr_noop") and args and isinstance(args[0], ast.Constant):
                    used.add(args[0].value)
                elif name == "trn" and len(args) >= 2 and isinstance(args[1], ast.Constant):
                    used.add(args[1].value)     # only the plural form is looked up
        return used

    def test_catalog_is_complete(self):
        from lmp.locales.tr import MESSAGES
        missing = sorted(m for m in self._used_msgids() if m not in MESSAGES)
        self.assertEqual(missing, [], "untranslated message ids")

    def test_placeholders_match(self):
        import re
        from lmp.locales.tr import MESSAGES
        field = re.compile(r"\{[a-z_]+(?::[^}]*)?\}")
        for en, tr_text in MESSAGES.items():
            self.assertEqual(sorted(field.findall(en)), sorted(field.findall(tr_text)), en)

    def test_language_switch(self):
        self.assertEqual(i18n.tr("Uninstaller"), "Uninstaller")
        i18n.set_language("tr")
        self.assertEqual(i18n.tr("Uninstaller"), "Kaldırıcı")
        self.assertEqual(i18n.tr("not in the catalog"), "not in the catalog")
        self.assertEqual(i18n.set_language("xx"), "en")        # unknown codes fall back
        self.assertEqual(i18n.trn("{n} item", "{n} items", 1), "{n} item")
        self.assertEqual(i18n.trn("{n} item", "{n} items", 2), "{n} items")
