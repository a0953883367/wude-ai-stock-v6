from pathlib import Path
HTML = Path('index.html').read_text(encoding='utf-8')

def test_no_neutral_fake_score_copy():
    banned = ['目前以中性 50 分呈現', "volume_score'],50", "institution_score'],50", "credit_score'],50"]
    for token in banned:
        assert token not in HTML
