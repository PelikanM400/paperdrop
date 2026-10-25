"""Execute UI state regressions with Node's standard library, never a browser.

Direct command: node --test tests/ui_state.cjs
Python-only installations can still run the ordinary suite without installing Node.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which('node'), 'UI state tests require Node (no npm/browser dependencies)')
class UIStateTests(unittest.TestCase):
    def test_inline_ui_state_regressions(self):
        from paperdrop import ui
        root = pathlib.Path(__file__).resolve().parent.parent
        env = os.environ.copy()
        env['PAPERDROP_UI_HTML'] = str(pathlib.Path(ui.__file__).parent / 'assets' / 'ui.html')
        result = subprocess.run(
            [shutil.which('node'), '--test', str(root / 'tests' / 'ui_state.cjs')],
            cwd=root, env=env, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
