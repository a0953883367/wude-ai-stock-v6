// The authenticated live service URL is injected here after cloud deployment.
// Never put broker IDs, API keys, passwords or certificates in this file.
window.WUDE_LIVE_API_BASE = window.WUDE_LIVE_API_BASE || 'https://wude-ai-stock-v6-production.up.railway.app';
window.WUDE_LIVE_POLL_MS = window.WUDE_LIVE_POLL_MS || 5000;

// 5371 and 3718 are different legal entities and security identifiers.  Keep
// all stored 5371 history/model records intact, but hide the retired security
// from the active dashboard.  Add a separate minimal 3718 official-close row
// until the new security has enough history for a complete model run.
(function installCoretronicTransitionBridge(){
  var nativeFetch=window.fetch.bind(window);
  var reportRow={
    symbol:'3718.TWO',name:'中光電投控',market:'TW',type:'個股',theme:'🚁 無人機',industry:'無人機',
    price:75.5,change_pct:-3.58,official_session_date:'2026-09-04',
    official_open_price:80,official_high_price:80.5,official_low_price:75.2,official_close_price:75.5,
    volume_pace:null,rsi:null,technical_score:null,fundamental_score:null,score:null,entry_score:null,
    action:'🟡 新代號資料建立中',risk:'新代號歷史資料不足，等待完整模型重算',
    overall_data_quality:'官方收盤價已更新；模型資料建立中'
  };
  var legacyRow={
    '排名':null,'股票':'中光電投控','代號':'3718.TWO','市場':'🇹🇼 台灣','類型':'個股',
    '主題':'🚁 無人機','次產業':'無人機','AI總分':null,'技術分數':null,'基本面分數':null,
    '現價':75.5,'漲跌幅':-3.58,'RSI':null,'相對量能':null,
    '操作建議':'🟡 新代號資料建立中','風險':'新代號歷史資料不足，等待完整模型重算'
  };
  function code(row){return String(row&&(row.symbol||row['代號'])||'').toUpperCase();}
  function replaceRows(rows,template){
    if(!Array.isArray(rows))return rows;
    var hasCurrent=rows.some(function(row){return code(row)==='3718.TWO';});
    var output=rows.filter(function(row){return code(row)!=='5371.TWO';});
    if(!hasCurrent)output.push(Object.assign({},template));
    if(hasCurrent){
      output=output.map(function(row){
        if(code(row)!=='3718.TWO')return row;
        var session=String(row.official_session_date||'');
        var stale=!session||session<'2026-09-04';
        return stale?Object.assign({},template):row;
      });
    }
    return output;
  }
  function repair(payload){
    if(Array.isArray(payload))return replaceRows(payload,legacyRow);
    if(!payload||typeof payload!=='object')return payload;
    if(Array.isArray(payload.data))payload.data=replaceRows(payload.data,reportRow);
    if(Array.isArray(payload.watchlist))payload.watchlist=replaceRows(payload.watchlist,reportRow);
    return payload;
  }
  window.fetch=function(input,init){
    return nativeFetch(input,init).then(function(response){
      var url=String(typeof input==='string'?input:(input&&input.url)||'');
      if(!response.ok||!/(stock_data\.json|reports\/(all_analysis|latest|rankings)\.json)(?:[?#]|$)/.test(url))return response;
      return response.clone().json().then(function(payload){
        return new Response(JSON.stringify(repair(payload)),{
          status:response.status,statusText:response.statusText,headers:response.headers
        });
      }).catch(function(){return response;});
    });
  };
})();
