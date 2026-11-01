"""Static checks that intra-package imports name the module defining them."""
import ast
import unittest
from pathlib import Path

INTEGRATION_PATH = Path(__file__).parents[1] / "custom_components" / "tether"


def _defined_names(statements: list[ast.stmt]) -> set[str]:
    names: set[str] = set()
    for node in statements:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.TypeAlias):
            names.add(node.name.id)
        elif isinstance(node, ast.If):
            names |= _defined_names(node.body) | _defined_names(node.orelse)
        elif isinstance(node, ast.Try):
            names |= _defined_names(node.body)
            for handler in node.handlers:
                names |= _defined_names(handler.body)
    return names


class RelativeImportTests(unittest.TestCase):
    def test_entity_callback_imports_support_minimum_home_assistant(self) -> None:
        for module in (
            "entity", "binary_sensor", "button", "light", "number", "select", "sensor"
        ):
            with self.subTest(module=module):
                tree = ast.parse(
                    (INTEGRATION_PATH / f"{module}.py").read_text(encoding="utf-8")
                )
                names = {
                    alias.name
                    for node in ast.walk(tree)
                    if isinstance(node, ast.ImportFrom)
                    and node.module == "homeassistant.helpers.entity_platform"
                    for alias in node.names
                }
                self.assertEqual(names, {"AddEntitiesCallback"})

    def test_relative_imports_resolve_to_defining_module(self) -> None:
        modules = {path.stem: path for path in INTEGRATION_PATH.glob("*.py")}
        definitions = {
            name: _defined_names(ast.parse(path.read_text(encoding="utf-8")).body)
            for name, path in modules.items()
        }
        for source_name, source_path in modules.items():
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or node.level != 1:
                    continue
                for alias in node.names:
                    with self.subTest(module=source_name, name=alias.name):
                        if node.module is None:
                            self.assertIn(alias.name, modules)
                        else:
                            self.assertIn(node.module, modules)
                            self.assertIn(alias.name, definitions[node.module])


if __name__ == "__main__":
    unittest.main()
