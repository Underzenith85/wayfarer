"""Protect domain boundaries while the engine grows."""
import ast
import importlib
from pathlib import Path
import subprocess
import sys
import unittest

import wayfarer


class ArchitectureTests(unittest.TestCase):
    def test_domain_imports_are_independent(self) -> None:
        package = Path(wayfarer.__file__).parent
        allowed = {
            'rules': {'rules', 'models'},
            'character': {'rules', 'character', 'models'},
            'simulation': {'rules', 'character', 'simulation', 'models'},
        }
        forbidden = {'sqlite3', 'http', 'urllib', 'socket', 'requests', 'httpx', 'openai', 'os'}
        for domain, dependencies in allowed.items():
            for source in (package / domain).rglob('*.py'):
                tree = ast.parse(source.read_text())
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        modules = [alias.name for alias in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        self.assertEqual(node.level, 0, f'Use absolute imports: {source}')
                        modules = [node.module or '']
                        if node.module == 'wayfarer':
                            modules += [f'wayfarer.{alias.name}' for alias in node.names]
                    else:
                        continue
                    for module in modules:
                        with self.subTest(source=source, module=module):
                            self.assertNotIn(module.split('.')[0], forbidden)
                            if module.startswith('wayfarer.'):
                                self.assertIn(module.split('.')[1], dependencies)

    def test_domain_import_does_not_load_adapters(self) -> None:
        subprocess.run([sys.executable, '-c', '''
import sys
import wayfarer.simulation.resolution
assert not any(m.startswith(('wayfarer.persistence', 'wayfarer.orchestration', 'wayfarer.transport')) for m in sys.modules)
assert 'sqlite3' not in sys.modules
'''], check=True)

    def test_all_packages_import(self) -> None:
        for name in ('rules', 'character', 'simulation', 'persistence', 'orchestration', 'transport'):
            importlib.import_module(f'wayfarer.{name}')
