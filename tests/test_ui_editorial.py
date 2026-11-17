"""Contracts for the approved editorial settings composition, without demo state."""
from html.parser import HTMLParser
import re
import unittest

import support  # noqa: F401
from paperdrop import ui


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.nodes = []
        self.stack = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = {'tag': tag, 'attrs': dict(attrs), 'parent': self.stack[-1] if self.stack else None}
        self.nodes.append(node)
        if tag not in ('meta', 'input', 'link', 'br', 'hr'):
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]['tag'] == tag:
                del self.stack[index:]
                break

    def with_class(self, name):
        return [node for node in self.nodes if name in node['attrs'].get('class', '').split()]


class EditorialPageTests(unittest.TestCase):
    def test_compact_controls_reserve_native_time_affordances_and_allow_translated_labels(self):
        time_match = re.search(r'\.time-row input\.control\{([^}]+)\}', ui.PAGE)
        button_match = re.search(r'\.btn\{([^}]+)\}', ui.PAGE)
        assert time_match is not None and button_match is not None
        # en-US native time includes AM/PM plus its picker icon, even with lang=en-GB.
        self.assertIn('width:128px;min-width:128px', time_match.group(1))
        self.assertIn('text-align:center', time_match.group(1))
        self.assertIn('width:auto;', button_match.group(1))
        self.assertIn('white-space:normal', ui.PAGE)

    def test_settings_are_one_group_with_stacked_description_control_rows(self):
        page = Elements(ui.PAGE)
        self.assertEqual(len(page.with_class('topbar')), 1, 'editorial top bar is missing')
        self.assertEqual(len(page.with_class('wordmark')), 1)
        settings = page.with_class('settings')
        self.assertEqual(len(settings), 1)
        rows = page.with_class('setting')
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row['parent'] is settings[0] for row in rows))
        self.assertEqual(len(page.with_class('section-caption')), 2)
        self.assertEqual(page.with_class('actions')[0]['parent'], settings[0])
        row_match = re.search(r'\.setting\{([^}]+)\}', ui.PAGE)
        self.assertIsNotNone(row_match)
        assert row_match is not None
        row_rule = row_match.group(1)
        self.assertIn('grid-template-columns:204px minmax(0,1fr)', row_rule)
        self.assertIn('--paper:#fff;', ui.PAGE)
        self.assertIn('--accent:#006842;', ui.PAGE)
        self.assertIn('@media(max-width:700px)', ui.PAGE)
        self.assertIn('.setting{display:block;', ui.PAGE)
        self.assertIn('prefers-reduced-motion:reduce', ui.PAGE)
        for demo in ('preview-note', 'mode-tag', 'simulated', '设计预览', '演示数据'):
            self.assertNotIn(demo, ui.PAGE)


if __name__ == '__main__':
    unittest.main()
