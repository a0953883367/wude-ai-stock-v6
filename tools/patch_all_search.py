from pathlib import Path

path = Path('index.html')
text = path.read_text(encoding='utf-8')
old = """function searchStock(){
  var q=document.getElementById('ticker').value.trim().toUpperCase();if(!q)return;
  var pool=STATE.allLegacy.map(normalizeLegacy);
  if(STATE.latest&&STATE.latest.watchlist){pool=pool.concat(STATE.latest.watchlist.map(normalizeReport));}
  var seen={},found=pool.filter(function(s){var key=String(s.symbol).toUpperCase();if(seen[key])return false;var hit=key===q||key===q+'.TW'||key===q+'.TWO'||String(s.name).toUpperCase().indexOf(q)>=0;if(hit)seen[key]=true;return hit;});
  document.getElementById('title').textContent='🔍 單股查詢：'+q;document.getElementById('status').textContent=found.length?'找到 '+found.length+' 檔':'找不到符合資料';renderStocks(found);
}
"""
new = """function buildAllAnalysisPool(){
  var merged={},ordered=[];
  function add(rows,normalizer){
    (rows||[]).forEach(function(row){
      var stock=normalizer(row),key=String(stock.symbol||'').toUpperCase();
      if(!key)return;
      if(!merged[key])ordered.push(key);
      merged[key]=Object.assign({},merged[key]||{},stock);
    });
  }
  add(STATE.allLegacy,normalizeLegacy);
  if(STATE.latest&&STATE.latest.watchlist)add(STATE.latest.watchlist,normalizeReport);
  if(STATE.normalized&&STATE.normalized.length)STATE.normalized.forEach(function(stock){
    var key=String(stock.symbol||'').toUpperCase();
    if(!key)return;
    if(!merged[key])ordered.push(key);
    merged[key]=Object.assign({},merged[key]||{},stock);
  });
  return sortByScore(ordered.map(function(key){return merged[key];}));
}
function searchStock(){
  var q=document.getElementById('ticker').value.trim().toUpperCase();if(!q)return;
  var pool=buildAllAnalysisPool();
  if(q==='ALL'){
    STATE.view='ALL';
    clearOtherNav('');
    document.getElementById('title').textContent='📚 ALL 全部股票分析';
    document.getElementById('status').textContent='共 '+pool.length+' 檔｜台股＋美股＋ETF｜依 AI 總分排序；資料不足欄位維持等待資料，不填假值';
    renderStocks(pool);
    return;
  }
  var seen={},found=pool.filter(function(s){var key=String(s.symbol).toUpperCase();if(seen[key])return false;var hit=key===q||key===q+'.TW'||key===q+'.TWO'||String(s.name).toUpperCase().indexOf(q)>=0;if(hit)seen[key]=true;return hit;});
  document.getElementById('title').textContent='🔍 單股查詢：'+q;document.getElementById('status').textContent=found.length?'找到 '+found.length+' 檔':'找不到符合資料';renderStocks(found);
}
"""
if old not in text:
    raise SystemExit('searchStock target not found; index.html changed')
text = text.replace(old,new,1)
text = text.replace('placeholder="輸入 NVDA、AMZN、2330、國巨"','placeholder="輸入 ALL、NVDA、AMZN、2330、國巨"',1)
text = text.replace('V6.29-S2','V6.29-ALL1')
path.write_text(text,encoding='utf-8')
print('ALL search patch applied')
