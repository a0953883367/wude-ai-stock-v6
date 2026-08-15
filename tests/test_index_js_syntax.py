from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import pytest


def test_index_inline_javascript_parses():
    node = shutil.which('node')
    if not node:
        pytest.skip('node not installed')
    html = Path('index.html').read_text(encoding='utf-8')
    scripts = re.findall(r'<script>(.*?)</script>', html, flags=re.S | re.I)
    assert scripts, 'no inline script found'
    source = '\n'.join(scripts)
    with tempfile.NamedTemporaryFile('w', suffix='.js', encoding='utf-8', delete=False) as f:
        f.write(source)
        path = f.name
    result = subprocess.run([node, '--check', path], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
