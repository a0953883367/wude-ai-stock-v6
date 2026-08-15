from pathlib import Path
HTML=Path('index.html').read_text(encoding='utf-8')
def test_navs():
    for x in ['marketButtons','quantumButtons','opticalButtons','shortButtons','longButtons','reportButtons']: assert x in HTML
def test_fixed_sections():
    for x in ['短線判斷','中長線判斷','技術／量價／籌碼／基本面','主力多空雷達','交易劇本','負面新聞風險雷達','第一買進區','第一支撐','第一壓力']: assert x in HTML
def test_no_fake_50():
    for x in ['目前以中性 50 分呈現',"volume_score'],50","institution_score'],50","credit_score'],50"]: assert x not in HTML
def test_same_card_for_missing():
    assert 'function placeholderStock' in HTML
    assert 'return stockCard(placeholderStock' in HTML
def test_version(): assert 'V6.29-S3' in HTML
