from pathlib import Path


def replace_between(text, start_marker, end_marker, replacement):
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    return text[:start] + replacement + text[end:]


# data_fetcher.py: load the full maintained search universe, not only Taiwan rows.
p = Path('data_fetcher.py')
text = p.read_text(encoding='utf-8')
replacement = '''def load_search_universe(path: Path = SETTINGS.search_data_path) -> list[dict[str, Any]]:
    """Load every Taiwan and US candidate maintained by the V6 dashboard."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in payload.get("data", []):
        symbol = str(row.get("代號", "")).strip().upper()
        market_text = str(row.get("市場", ""))
        if not symbol or symbol in seen:
            continue
        if "台灣" in market_text:
            if not (symbol.endswith(".TW") or symbol.endswith(".TWO")):
                continue
            market = "TW"
        elif "美國" in market_text:
            if symbol.endswith(".TW") or symbol.endswith(".TWO"):
                continue
            market = "US"
        else:
            continue
        seen.add(symbol)
        rows.append({
            "symbol": symbol,
            "name": row.get("股票", symbol),
            "market": market,
            "type": row.get("類型", "個股"),
            "theme": row.get("主題", "其他"),
            "industry": row.get("次產業", "其他"),
        })
    if not rows:
        raise RuntimeError("search_data.json 沒有可用的股票候選清單")
    return rows


def load_taiwan_universe(path: Path = SETTINGS.search_data_path) -> list[dict[str, Any]]:
    """Backward-compatible Taiwan-only view of the maintained search universe."""
    return [row for row in load_search_universe(path) if row.get("market") == "TW"]


'''
text = replace_between(text, 'def load_taiwan_universe(', 'def _chunks(', replacement)
p.write_text(text, encoding='utf-8')


# briefing.py: analyze the full search universe and publish the entire ranked result.
p = Path('briefing.py')
text = p.read_text(encoding='utf-8')
text = text.replace('    load_taiwan_universe,\n', '    load_search_universe,\n', 1)
old = '''    market_universe = load_taiwan_universe()\n    for item in market_universe:\n        item.setdefault("market", "TW")\n    watchlist = load_watchlist()\n\n    combined = {item["symbol"]: item for item in market_universe}\n'''
new = '''    search_universe = load_search_universe()\n    watchlist = load_watchlist()\n\n    combined = {item["symbol"]: item for item in search_universe}\n'''
if old not in text:
    raise SystemExit('briefing universe target not found')
text = text.replace(old, new, 1)
text = text.replace('        "universe_count": len(market_universe),\n', '        "universe_count": len(search_universe),\n', 1)
needle = '''    ranking_tmp.replace(ranking_path)\n\n    delivered = False if args.no_telegram else send_telegram(markdown)\n'''
insert = '''    ranking_tmp.replace(ranking_path)\n\n    all_analysis_payload = {\n        "updated_at": report["updated_at"],\n        "period": args.period,\n        "candidate_count": len(universe),\n        "analyzed_count": len(ranked),\n        "unavailable_count": max(0, len(universe) - len(ranked)),\n        "data": ranked,\n    }\n    all_analysis_path = SETTINGS.reports_dir / "all_analysis.json"\n    all_analysis_tmp = SETTINGS.reports_dir / "all_analysis.tmp"\n    all_analysis_tmp.write_text(\n        json.dumps(all_analysis_payload, ensure_ascii=False, separators=(",", ":")),\n        encoding="utf-8",\n    )\n    all_analysis_tmp.replace(all_analysis_path)\n\n    delivered = False if args.no_telegram else send_telegram(markdown)\n'''
if needle not in text:
    raise SystemExit('briefing rankings target not found')
text = text.replace(needle, insert, 1)
p.write_text(text, encoding='utf-8')


# index.html: prefer the full all_analysis output for ALL and single-stock search.
p = Path('index.html')
text = p.read_text(encoding='utf-8')
text = text.replace("var STATE={stockData:null,allLegacy:[],normalized:[],tw:[],us:[],etf:[],quantum:null,optical:null,latest:null,reports:{},view:'TW'};",
                    "var STATE={stockData:null,allLegacy:[],normalized:[],allAnalysis:null,tw:[],us:[],etf:[],quantum:null,optical:null,latest:null,reports:{},view:'TW'};", 1)
needle = '''  if(STATE.normalized&&STATE.normalized.length)STATE.normalized.forEach(function(stock){\n    var key=String(stock.symbol||'').toUpperCase();\n    if(!key)return;\n    if(!merged[key])ordered.push(key);\n    merged[key]=Object.assign({},merged[key]||{},stock);\n  });\n  return sortByScore(ordered.map(function(key){return merged[key];}));\n'''
replacement = '''  if(STATE.normalized&&STATE.normalized.length)STATE.normalized.forEach(function(stock){\n    var key=String(stock.symbol||'').toUpperCase();\n    if(!key)return;\n    if(!merged[key])ordered.push(key);\n    merged[key]=Object.assign({},merged[key]||{},stock);\n  });\n  if(STATE.allAnalysis&&Array.isArray(STATE.allAnalysis.data)){\n    add(STATE.allAnalysis.data,normalizeReport);\n  }\n  return sortByScore(ordered.map(function(key){return merged[key];}));\n'''
if needle not in text:
    raise SystemExit('index buildAllAnalysisPool target not found')
text = text.replace(needle, replacement, 1)
text = text.replace("document.getElementById('status').textContent='共 '+pool.length+' 檔｜台股＋美股＋ETF｜依 AI 總分排序；資料不足欄位維持等待資料，不填假值';",
                    "var fullCount=STATE.allAnalysis&&Array.isArray(STATE.allAnalysis.data)?STATE.allAnalysis.data.length:0;document.getElementById('status').textContent='共 '+pool.length+' 檔｜完整分析 '+fullCount+' 檔｜台股＋美股＋ETF｜依 AI 總分排序；真正缺資料者才顯示等待資料';", 1)
old_pair = '''      fetchJSON('optical_watchlist.json').catch(function(){return null;})\n    ]);\n    var live=pair[0],raw=pair[1],all=Array.isArray(raw)?raw:(Array.isArray(raw.data)?raw.data:[]);\n    STATE.stockData=raw;STATE.allLegacy=all;STATE.latest=pair[2];STATE.quantum=pair[3];STATE.optical=pair[4];if(STATE.latest)STATE.reports[STATE.latest.period]=STATE.latest;\n'''
new_pair = '''      fetchJSON('optical_watchlist.json').catch(function(){return null;}),\n      fetchJSON('reports/all_analysis.json').catch(function(){return null;})\n    ]);\n    var live=pair[0],raw=pair[1],all=Array.isArray(raw)?raw:(Array.isArray(raw.data)?raw.data:[]);\n    STATE.stockData=raw;STATE.allLegacy=all;STATE.latest=pair[2];STATE.quantum=pair[3];STATE.optical=pair[4];STATE.allAnalysis=pair[5];if(STATE.latest)STATE.reports[STATE.latest.period]=STATE.latest;\n'''
if old_pair not in text:
    raise SystemExit('index loadAll target not found')
text = text.replace(old_pair, new_pair, 1)
text = text.replace('V6.29-ALL1', 'V6.29-ALL2')
p.write_text(text, encoding='utf-8')


# Add a regression test for the full maintained universe.
test = Path('tests/test_full_universe.py')
test.write_text('''import json\n\nfrom config import SETTINGS\nfrom data_fetcher import load_search_universe, load_taiwan_universe\n\n\ndef test_search_universe_loads_all_maintained_markets():\n    payload = json.loads(SETTINGS.search_data_path.read_text(encoding="utf-8"))\n    rows = load_search_universe()\n    symbols = {row["symbol"] for row in rows}\n    assert len(rows) == payload["total"]\n    assert len(symbols) == len(rows)\n    assert {row["market"] for row in rows} == {"TW", "US"}\n    assert any(row["type"] == "ETF" for row in rows)\n    assert len(load_taiwan_universe()) == payload["summary"]["台灣個股"] + payload["summary"]["台灣ETF"]\n''', encoding='utf-8')

print('ALL analysis v2 patch applied')
