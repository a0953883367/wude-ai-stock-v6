from pathlib import Path

HTML = Path('index.html').read_text(encoding='utf-8')


def test_all_navigation_sections_present():
    for token in ['marketButtons','quantumButtons','opticalButtons','shortButtons','longButtons','reportButtons']:
        assert token in HTML


def test_missing_data_is_not_faked():
    assert "目前以中性 50 分呈現" not in HTML
    assert "volume_score'],50" not in HTML
    assert "institution_score'],50" not in HTML
    assert "credit_score'],50" not in HTML
    assert '缺資料只顯示「等待資料」' in HTML


def test_complete_card_sections_are_fixed():
    for label in ['短線判斷','中長線判斷','技術／量價／籌碼／基本面','主力多空雷達','交易劇本','負面新聞風險雷達','第一買進區','第一支撐','第一壓力']:
        assert label in HTML


def test_concept_missing_items_use_same_card():
    assert 'function placeholderStock' in HTML
    assert 'return stockCard(placeholderStock' in HTML


def test_version_bumped():
    assert 'V6.29-S3' in HTML
