"""Offline renderer/startup regression checks; never import live server/config."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from core import renderer


def load_launcher():
    spec = importlib.util.spec_from_file_location('mobile_launcher_test', APP / 'run.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MobileOutputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name) / 'خروجی'
        self.chunks = [
            {'idx': 2, 'state': 'done', 'heading': '<img src=x onerror=alert(1)>',
             'arabic_text': 'عربي\nسطر ثان', 'persian_text': '# عنوان\n**تدریس** فارسی\n- یک\n- دو\n#پایان'},
            {'idx': 0, 'state': 'completed', 'arabic_text': 'اول', 'persian_text': 'اولین تدریس'},
            {'idx': 1, 'state': 'failed', 'persian_text': 'PARTIAL MUST NOT LEAK'},
        ]

    def test_all_four_artifacts_reopen_and_preserve_order(self):
        from docx import Document
        paths = renderer.render_all_outputs(self.chunks, 'عنوان <script> & {CONTENT_BLOCKS}', str(self.base))
        self.assertEqual(len(paths), 4)
        for key, value in paths.items():
            path = Path(value)
            self.assertGreater(path.stat().st_size, 0)
            if key.endswith('docx'):
                doc = Document(path)
                text = '\n'.join(p.text for p in doc.paragraphs)
                self.assertNotIn('PARTIAL', text)
                self.assertNotIn('#پایان', text)
                self.assertNotIn('**تدریس**', text)
                self.assertTrue(any(r.bold and r.text == 'تدریس' for p in doc.paragraphs for r in p.runs))
                if key.startswith('khales'):
                    self.assertNotIn('عربي', text)
                else:
                    self.assertIn('عربي\nسطر ثان', text)
                self.assertLess(text.index('اولین تدریس'), text.index('تدریس فارسی'))
            else:
                text = path.read_text(encoding='utf-8')
                self.assertIn('عنوان &lt;script&gt; &amp; {CONTENT_BLOCKS}', text)
                self.assertNotIn('<img src=x', text)
                self.assertNotIn('PARTIAL', text)
                self.assertNotIn('#پایان', text)
                self.assertIn('<ul>\n<li>یک</li>\n<li>دو</li>\n</ul>', text)
                self.assertLess(text.index('id="chunk-1"'), text.index('id="chunk-3"'))
                self.assertEqual('class="arabic-box"' in text, not key.startswith('khales'))

    def test_render_failure_propagates_and_preserves_previous_files(self):
        paths = renderer.render_all_outputs(self.chunks, 'title', str(self.base))
        before = {key: Path(value).read_bytes() for key, value in paths.items()}
        with patch.object(renderer, 'render_docx_document', side_effect=OSError('disk error')):
            with self.assertRaisesRegex(OSError, 'disk error'):
                renderer.render_all_outputs(self.chunks, 'new title', str(self.base))
        self.assertEqual(before, {key: Path(value).read_bytes() for key, value in paths.items()})
        self.assertFalse(list(self.base.parent.glob('.jozve-render-*')))

    def test_atomic_single_file_failure_preserves_original(self):
        target = self.base.with_suffix('.html')
        target.write_text('old', encoding='utf-8')
        def fail(path):
            Path(path).write_text('partial', encoding='utf-8')
            raise OSError('failed write')
        with self.assertRaises(OSError):
            renderer._atomic_write(target, fail)
        self.assertEqual(target.read_text(encoding='utf-8'), 'old')

    def test_completed_indices_must_be_numeric_unique_and_nonnegative(self):
        for idx in ['1 onclick=alert(1)', -1, True]:
            with self.subTest(idx=idx), self.assertRaises(ValueError):
                renderer.render_html_document([{'idx': idx, 'state': 'done', 'persian_text': 'x'}], 't', False, str(self.base))
        with self.assertRaises(ValueError):
            renderer.render_all_outputs([self.chunks[0], self.chunks[0]], 't', str(self.base))

    def test_empty_or_partial_output_is_not_success(self):
        for chunks in [[], [self.chunks[-1]], [{'idx': 0, 'state': 'done', 'persian_text': '#پایان'}]]:
            with self.subTest(chunks=chunks), self.assertRaises(ValueError):
                renderer.render_all_outputs(chunks, 't', str(self.base))

    def test_safe_markdown_lists_and_html(self):
        rendered = renderer.markdown_to_html_paragraphs('- **one**\n- <script>alert(1)</script>\n\n1. first\n2. second\ntext')
        self.assertIn('<ul>', rendered)
        self.assertIn('</ul>\n<ol>', rendered)
        self.assertIn('<strong>one</strong>', rendered)
        self.assertNotIn('<script>', rendered)
        self.assertTrue(rendered.endswith('</ol>\n<p>text</p>'))

    def test_launcher_validates_ports_without_importing_server(self):
        import argparse
        launcher = load_launcher()
        self.assertEqual(launcher.port_number('8080'), 8080)
        for value in ['0', '-1', '65536', 'abc']:
            with self.assertRaises(argparse.ArgumentTypeError):
                launcher.port_number(value)
        result = subprocess.run([sys.executable, str(APP / 'run.py'), '--help'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--no-browser', result.stdout)

    def test_javascript_regressions(self):
        node = os.environ.get('JOZVE_TEST_NODE') or shutil.which('node')
        if not node:
            self.skipTest('Node unavailable; set JOZVE_TEST_NODE for UI behavioral tests')
        result = subprocess.run([node, str(Path(__file__).with_name('test_mobile_ui.cjs'))], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('mobile UI regression checks passed', result.stdout)


if __name__ == '__main__':
    unittest.main()
