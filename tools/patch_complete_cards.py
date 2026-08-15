from pathlib import Path
import re

p=Path('index.html')
s=p.read_text(encoding='utf-8')
orig=s

# 1) 缺資料不再用 50 分代填
s=s.replace("volumeScore:first(row,['量能分數','volume_score'],50)","volumeScore:first(row,['量能分數','volume_score'],'—')")
s=s.replace("institutionScore:first(row,['籌碼分數','institution_score'],50)","institutionScore:first(row,['籌碼分數','institution_score'],'—')")
s=s.replace("creditScore:first(row,['信用分數','credit_score'],50)","creditScore:first(row,['信用分數','credit_score'],'—')")
s=s.replace("valuationScore:first(row,['估值分數','valuation_score'],50)","valuationScore:first(row,['估值分數','valuation_score'],'—')")
s=s.replace("growthScore:first(row,['成長分數','growth_score'],50)","growthScore:first(row,['成長分數','growth_score'],'—')")
s=s.replace("financialQualityScore:first(row,['財務品質分數','financial_quality_score'],50)","financialQualityScore:first(row,['財務品質分數','financial_quality_score'],'—')")

# 2) 深入分析缺資料顯示等待資料，不再宣稱中性 50 分
s=s.replace("function bounded(value){var n=Number(value);return Number.isFinite(n)?Math.max(0,Math.min(100,n)):50;}","function bounded(value){var n=Number(value);return Number.isFinite(n)?Math.max(0,Math.min(100,n)):null;}")
s=s.replace("function scoreTone(value){var n=bounded(value);return n>=70?'強':n>=58?'偏強':n>=45?'中性':'偏弱';}","function scoreTone(value){var n=bounded(value);if(n===null)return '等待資料';return n>=70?'強':n>=58?'偏強':n>=45?'中性':'偏弱';}")
s=s.replace("return point(a,bounded(values[i])/100);","var v=bounded(values[i]);return point(a,(v===null?0:v)/100);")
s=s.replace("style=\"width:'+bounded(f[1])+'%\"","style=\"width:'+(bounded(f[1])===null?0:bounded(f[1]))+'%\"")
s=s.replace("⚠️ '+missing.join('、')+'尚未取得完整資料，目前以中性 50 分呈現，不會因此扣分。","⚠️ '+missing.join('、')+'尚未取得完整資料，欄位保留並顯示「等待資料」，不代填 50 分。")

# 3) 固定卡片：條件資料缺少時仍顯示完整區塊
needle="  var longPlan=stock.midLongStatus?"
insert="  if(!outlook)outlook='<div class=\"databox\"><b>📈 短期方向</b><br><span class=\"waiting\">等待資料</span>｜不虛構看漲／看跌判斷</div>';\n"
assert needle in s
s=s.replace(needle,insert+needle,1)

needle="  var shortPlan=stock.shortTermStatus?"
insert="  if(!longPlan)longPlan='<div class=\"databox\">🌳 <b>中長線判斷</b><br>中長線分數：<span class=\"waiting\">等待資料</span><br>第一布局區：等待資料<br>第二布局區：等待資料<br>資金配置：等待資料<br>風控／第一目標／第二目標：等待資料</div>';\n"
assert needle in s
s=s.replace(needle,insert+needle,1)

needle="  var change=Number.isFinite(Number(stock.change))?percent(stock.change):esc(stock.change);"
insert="  if(!shortPlan)shortPlan='<div class=\"databox\">⚡ <b>短線判斷</b><br>短線分數：<span class=\"waiting\">等待資料</span><br>進場區／觸發／停損／第一停利／第二停利：等待資料</div>';\n"
assert needle in s
s=s.replace(needle,insert+needle,1)

needle="  var positioning='';"
insert="  if(!data)data='<div class=\"databox\"><b>📊 技術／量價／籌碼／基本面</b><br>均線／均量／K線／法人／信用／基本面／財務品質／分點：<span class=\"waiting\">等待資料</span></div>';\n"
assert needle in s
s=s.replace(needle,insert+needle,1)

needle="  var scenario=stock.scenarioContinuation?"
insert="  if(!positioning)positioning='<div class=\"databox\">🎯 <b>主力多空雷達</b><br><span class=\"waiting\">等待資料</span>｜資料不足時不虛構主力方向</div>';\n"
assert needle in s
s=s.replace(needle,insert+needle,1)

needle="  var newsLevel=stock.newsRiskLevel||'⚪ 尚未掃描';"
insert="  if(!scenario)scenario='<div class=\"databox\">📋 <b>交易劇本</b><br>依據／續強／不追價／跌破條件：<span class=\"waiting\">等待資料</span></div>';\n"
assert needle in s
s=s.replace(needle,insert+needle,1)

# 4) 缺行情的概念股也走同一 stockCard
marker="function quantumSymbol(value){return String(value||'').toUpperCase().replace(/\\\\.(TW|TWO)$/,'');}\n"
placeholder="""function placeholderStock(item,market,risk){
  return {name:item.name||'—',symbol:item.symbol||'—',market:market||'US',type:String(item.symbol||'').toUpperCase().indexOf('ETF')>=0?'ETF':'個股',theme:item.classification||'概念觀察',score:'—',entryScore:'—',price:'—',change:'—',rsi:'—',volume:'—',technical:'—',fundamental:'—',news:'等待資料',buy:'—',better:'—',support1:'—',support2:'—',resistance1:'—',resistance2:'—',stop:'—',advice:'⚪ 資料待補｜目前僅列入觀察，不作為立即買進依據',risk:risk||item.role||'資料待補',light:'⚪',source:'placeholder'};
}
"""
assert marker in s
s=s.replace(marker,marker+placeholder,1)

s=re.sub(r"function quantumPlaceholder\(item,index\)\{.*?\n\}\nfunction showQuantum", "function quantumPlaceholder(item,index){var m=String(item.symbol||'').match(/\\.(TW|TWO)$/i)?'TW':'US';return stockCard(placeholderStock(item,m,'量子產業仍在早期，波動與技術路線風險高'),index);}\nfunction showQuantum", s, count=1, flags=re.S)
s=re.sub(r"function opticalPlaceholder\(item,index\)\{.*?\n\}\nfunction showOptical", "function opticalPlaceholder(item,index){var m=String(item.symbol||'').match(/\\.(TW|TWO)$/i)?'TW':'US';return stockCard(placeholderStock(item,m,'注意 AI 資本支出、800G／1.6T需求、庫存循環與估值波動'),index);}\nfunction showOptical", s, count=1, flags=re.S)

# 5) 早中晚報 unavailable 也用完整卡片
old="if(report.unavailable&&report.unavailable.length){html+='<div class=\"group\">⚪ 暫無可靠行情</div><div class=\"empty\">'+report.unavailable.map(function(x){return esc(x.name)+'('+esc(marketCode(x.symbol,x.market))+')';}).join('、')+'</div>';}"
new="if(report.unavailable&&report.unavailable.length){html+='<div class=\"group\">⚪ 暫無可靠行情</div>'+report.unavailable.map(function(x,i){return stockCard(placeholderStock(x,x.market||'US','暫無可靠行情，等待資料'),i);}).join('');}"
assert old in s
s=s.replace(old,new,1)

# 6) 版本與 CSS
s=s.replace('V6.29-S2','V6.29-S3')
s=s.replace('.risk{color:#fbbf24;', '.waiting{color:#fbbf24;font-weight:800}.risk{color:#fbbf24;',1)

# 驗收字串
for token in ['短線判斷','中長線判斷','技術／量價／籌碼／基本面','主力多空雷達','交易劇本','function placeholderStock','V6.29-S3']:
    assert token in s, token
for token in ['目前以中性 50 分呈現',"volume_score'],50","institution_score'],50","credit_score'],50"]:
    assert token not in s, token
assert s!=orig
p.write_text(s,encoding='utf-8')
print('patched index.html safely')
