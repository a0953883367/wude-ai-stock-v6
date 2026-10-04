import pathlib,json,gzip,hashlib,datetime,sys,collections
from zoneinfo import ZoneInfo
root=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
from weight_experiment import select_picks
state=json.loads((root/'reports/tw_weight_experiment.json').read_text());ref=json.loads((root/'reports/tw_weight_price_audit.json').read_text());sessions=ref['calendar']['sessions'];archives=[]
for p in sorted((root/'reports/archive').glob('*json*')):
 try:
  b=p.read_bytes(); a=json.loads(gzip.decompress(b) if p.suffix=='.gz' else b)
  archives.append((p.name,a,hashlib.sha256(b).hexdigest()))
 except Exception:pass
result={'checked_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':'partial','models':{},'archives_checked':len(archives),'no_ranking_changes':True,'raw_observations_unchanged':True}
for name,m in state['models'].items():
 excluded={x['raw_index'] for x in m['session_validation']['excluded_days']};dates=[];positions=[]
 for i,d in enumerate(m['days']):
  if i in excluded:continue
  signal=d['signal_session_date'];entry=d['session_date'];wanted=d['ranking_snapshot_id'];matches=[];same_day=0
  for filename,a,archive_digest in archives:
   if not str(a.get('updated_at','')).startswith(signal):continue
   same_day+=1;picks=select_picks(a.get('watchlist',[]),m['accumulation_weight'])
   payload='|'.join(f"{p['rank']}:{p['symbol']}:{p['ranking_score']}:{p['rank_tier']}" for p in picks)
   digest=hashlib.sha256(f"{m['key']}|{signal}|{payload}".encode()).hexdigest()[:12]
   if digest==wanted:
    timestamp=datetime.datetime.fromisoformat(a['updated_at']);timestamp=timestamp.replace(tzinfo=ZoneInfo('Asia/Taipei')) if timestamp.tzinfo is None else timestamp;before=timestamp<datetime.datetime.fromisoformat(entry+'T09:00:00').replace(tzinfo=ZoneInfo('Asia/Taipei'))
    source_dates={r.get('official_session_date') for r in a.get('watchlist',[]) if r['symbol'] in {p['symbol'] for p in picks}}
    matches.append({'archive':filename,'archive_file_sha256':archive_digest,'updated_at':a['updated_at'],'before_entry_open':before,'source_dates':sorted(str(x) for x in source_dates),'all_signal_dates_match':source_dates=={signal}})
  dates.append({'entry_date':entry,'signal_date':signal,'snapshot_id':wanted,'signal_created_at_present':'signal_created_at' in d,'archives_on_signal_date':same_day,'exact_snapshot_matches':matches,'status':'matching_archive_before_entry' if any(x['before_entry_open'] and x['all_signal_dates_match'] for x in matches) else 'snapshot_timing_not_verified'})
  prev=sessions[sessions.index(entry)-1] if entry in sessions and sessions.index(entry)>0 else None
  for p in d['positions']:
   bars=ref['prices'].get(p['symbol'],{});first=bars.get(entry,{});previous=bars.get(prev,{})
   if not first or not previous or not p.get('data_available'):continue
   gap=(first['open']/previous['close']-1)*100;ret=(first['close']/first['open']-1)*100
   positions.append({'date':entry,'symbol':p['symbol'],'signal_date':signal,'prior_session':prev,'signal_is_prior_session':signal==prev,'gap_pct':round(gap,4),'session_gross_return_pct':round(ret,4),'prev_close':previous['close'],'open':first['open'],'close':first['close'],'group':'gap_up_gt_2pct' if gap>2 else 'other','source_sha256':[previous.get('response_sha256') or previous.get('evidence_sha256'),first.get('response_sha256') or first.get('evidence_sha256')]})
 supported={x['entry_date'] for x in dates if x['status']=='matching_archive_before_entry'}
 groups={}
 supported_groups={}
 for group in ['gap_up_gt_2pct','other']:
  xs=[p for p in positions if p['group']==group and p['signal_is_prior_session']]; groups[group]={'samples':len(xs),'average_session_gross_return_pct':round(sum(p['session_gross_return_pct'] for p in xs)/len(xs),4) if xs else None,'negative_samples':sum(p['session_gross_return_pct']<0 for p in xs)}
 for group in ['gap_up_gt_2pct','other']:
  xs=[p for p in positions if p['group']==group and p['signal_is_prior_session'] and p['date'] in supported];supported_groups[group]={'samples':len(xs),'average_session_gross_return_pct':round(sum(p['session_gross_return_pct'] for p in xs)/len(xs),4) if xs else None}
 result['models'][name]={'days':dates,'timing_counts':dict(collections.Counter(d['status'] for d in dates)),'gap_comparison':groups,'archive_supported_gap_comparison':supported_groups,'positions':positions,'causal_conclusion_allowed':False,'limitations':['archived_report_timestamp_is_not_proof_of_first_pending_creation','snapshot_digest_reconstruction_uses_current_selection_recipe','partial_official_price_sample_selection_bias','gap_is_not_intraday_execution_slippage','no_alternative_entry_price_or_strategy_performance_claim']}
(root/'reports/tw_weight_signal_timing_audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
for name,m in result['models'].items():print(name,m['timing_counts'],m['gap_comparison'])
