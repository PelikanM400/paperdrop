"""Installer ownership, independent of shell environment and executable names."""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Optional


def homebrew_opt_prefix() -> Optional[Path]:
    """Read the Formula's package-local marker without executing Python code.

    The source distribution has no marker. Only a single literal OPT_PREFIX
    assignment is accepted; unknown or malformed installations remain unknown.
    Keep the opt symlink spelling: resolving it would pin jobs to an old keg.
    """
    try:
        marker = Path(__file__).with_name('_homebrew.py')
        if marker.is_symlink() or not marker.is_file():
            return None
        with marker.open('rb') as stream:
            data = stream.read(4097)
        if len(data) > 4096:
            return None
        tree = ast.parse(data.decode('utf-8'))
        statement, = tree.body
        if (not isinstance(statement, ast.Assign) or len(statement.targets) != 1
                or not isinstance(statement.targets[0], ast.Name)
                or statement.targets[0].id != 'OPT_PREFIX'
                or not isinstance(statement.value, ast.Constant)
                or not isinstance(statement.value.value, str)):
            return None
        value = statement.value.value
        prefix = Path(value)
        if (not prefix.is_absolute() or value.startswith('//') or str(prefix) != value
                or prefix.parts[-2:] != ('opt', 'paperdrop')
                or '..' in prefix.parts or 'Cellar' in prefix.parts
                or ':' in value or any(ord(c) < 32 or ord(c) == 127 for c in value)):
            return None
        return prefix
    except (OSError, ValueError, TypeError, SyntaxError, UnicodeError):
        return None
