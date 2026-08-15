from pathlib import Path
import re,shutil,subprocess,tempfile,pytest
def test_js():
    node=shutil.which('node')
    if not node: pytest.skip('node missing')
    html=Path('index.html').read_text(encoding='utf-8')
    scripts=re.findall(r'<script>(.*?)</script>',html,flags=re.S|re.I)
    assert scripts
    with tempfile.NamedTemporaryFile('w',suffix='.js',encoding='utf-8',delete=False) as f:
        f.write('\n'.join(scripts)); path=f.name
    r=subprocess.run([node,'--check',path],capture_output=True,text=True)
    assert r.returncode==0,r.stderr
