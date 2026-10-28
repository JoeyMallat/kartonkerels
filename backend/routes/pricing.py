from flask import Blueprint
from ..core import *

bp = Blueprint("pricing", __name__)

with conn() as c:
 c.execute('CREATE TABLE IF NOT EXISTS pkmn_sales_cache(card_id TEXT NOT NULL,grade INTEGER NOT NULL,fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,sales TEXT NOT NULL,PRIMARY KEY(card_id,grade))')

def sale_date(x):
 return str(x.get('sold_at') or x.get('sold_date') or x.get('date') or x.get('ended_at') or x.get('created_at') or '')

def latest_sales(listings,grade,limit=5):
 valid=[x for x in listings if x.get('attribution')=='exact' and str(x.get('grader','')).upper()=='PSA'
  and str(x.get('grade'))==str(grade) and x.get('grade_qualifier') in (None,'')
  and isinstance(x.get('price'),(int,float)) and x['price']>0
  and str(x.get('currency','')).upper() in ('USD','EUR')]
 valid.sort(key=sale_date,reverse=True)
 return valid[:limit]

@bp.get('/api/pkmn/usage')
def pkmn_usage():
 with conn() as c:
  spent=c.execute("SELECT COALESCE(SUM(credits),0) FROM pkmn_usage WHERE date(used_at)=date('now')").fetchone()[0]
 return jsonify(today=spent,key_set=bool(setting('pkmn_api_key') or os.getenv('PKMNPRICES_API_KEY')))


@bp.get('/api/cards/<path:cid>/auto-prices')
def auto_prices(cid):
 with conn() as c:
  card=c.execute('SELECT * FROM cards WHERE id=?',(cid,)).fetchone()
  if not card:return err('Card not found',404)
  card=dict(card)
  link=c.execute('SELECT provider_id FROM pkmn_links WHERE card_id=?',(cid,)).fetchone()
  cache=c.execute('SELECT checked_at,message FROM auto_price_status WHERE card_id=?',(cid,)).fetchone()
  if cache and request.args.get('force')!='1':
   try:
    if (datetime.datetime.utcnow()-datetime.datetime.fromisoformat(cache['checked_at'])).total_seconds()<86400:
     return jsonify(status='cached',message=cache['message'])
   except (TypeError,ValueError):pass
 if not (setting('pkmn_api_key') or os.getenv('PKMNPRICES_API_KEY')):
  return jsonify(status='unconfigured',message='Set PkmnPrices API key in Settings.')
 try:
  pid=link['provider_id'] if link else None
  if not pid:
   number=str(card['number'] or '').split('/')[0].lstrip('0') or '0'
   # First search narrow; fallback to name only when number/total differs between catalogs.
   response,_=pkmn_request('/cards',{'name':card['name'],'number':number,'per_page':25})
   options=response.get('data',[])
   pid=pick_match(card,options)
   if not pid:
    response,_=pkmn_request('/cards',{'name':card['name'],'per_page':100})
    pid=pick_match(card,response.get('data',[]))
   if not pid:raise ValueError('No matching printing found in PkmnPrices.')
  detail,_=pkmn_request('/cards/'+pid,{'currency':'usd'})
  if isinstance(detail.get('data'),dict):detail=detail['data']
  if name_score(detail.get('name'),card['name'])<.7:raise ValueError('Provider card mismatch.')
  raw,currency,variant=extract_raw_prices(detail)
  if raw is None:raise ValueError('PkmnPrices has no raw market price for this card.')
  # Fetch the latest 5 eBay sold listings per PSA grade (each listing costs one credit),
  # cache them for the card page, and never combine USD and EUR comps in one median.
  grade_values={};grade_counts={};grade_errors=[]
  for grade in (7,8,9,10):
   try:
    sold,_=pkmn_request('/cards/'+pid+'/listings/ebay',{'graded':'true','grader':'PSA','grade':str(grade),'period':'90d','limit':5})
    valid=latest_sales(sold.get('data',[]),grade)
    with conn() as c:c.execute('INSERT INTO pkmn_sales_cache(card_id,grade,fetched_at,sales) VALUES(?,?,CURRENT_TIMESTAMP,?) ON CONFLICT(card_id,grade) DO UPDATE SET fetched_at=CURRENT_TIMESTAMP,sales=excluded.sales',(cid,grade,json.dumps(valid)))
    # Prefer the raw card currency; if unavailable, choose the currency with more comps.
    buckets={cur:[float(x['price']) for x in valid if str(x['currency']).upper()==cur] for cur in ('USD','EUR')}
    target=currency if len(buckets.get(currency,[]))>=2 else max(buckets,key=lambda c:len(buckets[c]))
    if len(buckets[target])>=2:
     # Store only when raw and graded currencies match, to avoid mixed-currency profit math.
     if target==currency:
      grade_values[grade]=round(statistics.median(buckets[target]),2)
      grade_counts[grade]=len(buckets[target])
     else:grade_errors.append(f'PSA {grade}: sales only in {target}, raw in {currency}')
   except ValueError as e:
    grade_errors.append(f'PSA {grade}: {e}')
    if 'limit' in str(e).lower() or 'plan' in str(e).lower():break
  with conn() as c:
   c.execute('INSERT INTO pkmn_links(card_id,provider_id) VALUES(?,?) ON CONFLICT(card_id) DO UPDATE SET provider_id=excluded.provider_id',(cid,pid))
   values=[grade_values.get(g) for g in (7,8,9,10)]
   c.execute('UPDATE cards SET raw=?,price_currency=?,psa7=?,psa8=?,psa9=?,psa10=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(raw,currency,*values,cid))
   c.execute("INSERT INTO price_history(card_id,recorded_at,raw,psa7,psa8,psa9,psa10,source) VALUES(?,CURRENT_TIMESTAMP,?,?,?,?,?,'pkmnprices-pro')",(cid,raw,*values))
   message=f'{currency} raw price updated ({variant or "standard"}). '+(', '.join(f'PSA {g}: {n} sales' for g,n in grade_counts.items()) if grade_counts else 'No sufficient exact PSA sales.')
   if grade_errors:message+=' '+ '; '.join(grade_errors)
   c.execute('INSERT INTO auto_price_status(card_id,checked_at,message) VALUES(?,CURRENT_TIMESTAMP,?) ON CONFLICT(card_id) DO UPDATE SET checked_at=CURRENT_TIMESTAMP,message=excluded.message',(cid,message))
  return jsonify(status='updated',message=message,raw=raw,currency=currency,variant=variant,grades=grade_values,sample_counts=grade_counts)
 except ValueError as e:
  # Cache failed lookups for one hour rather than a whole day.
  return jsonify(status='unavailable',message=str(e))
 except Exception as e:
  app.logger.exception('PkmnPrices lookup failed for card %s',cid)
  return jsonify(status='unavailable',message='Provider error; check server logs. No prices overwritten.'),502


@bp.post('/api/cards/<path:cid>/pkmn/search')
def pkmn_search(cid):
 with conn() as c:card=c.execute('SELECT * FROM cards WHERE id=?',(cid,)).fetchone()
 if not card:return err('Card not found',404)
 try:
  # Search returns catalog matches, NOT prices. Small limit saves credits.
  result,remaining=pkmn_request('/cards',{'name':card['name'],'per_page':5})
  return jsonify(matches=result.get('data',[]),remaining=remaining,notice='Confirm exact set, number, language and printing before linking.')
 except ValueError as e:return err(str(e),400)
 except Exception:return err('Provider temporarily unavailable',502)


@bp.post('/api/cards/<path:cid>/pkmn/link')
def pkmn_link(cid):
 d=request.get_json(silent=True) or {}
 pid=str(d.get('provider_id',''))
 if not pid.isdigit():return err('Select a valid provider card ID')
 with conn() as c:
  if not c.execute('SELECT 1 FROM cards WHERE id=?',(cid,)).fetchone():return err('Card not found',404)
  c.execute('INSERT INTO pkmn_links(card_id,provider_id) VALUES(?,?) ON CONFLICT(card_id) DO UPDATE SET provider_id=excluded.provider_id',(cid,pid))
 return jsonify(ok=True)


@bp.post('/api/cards/<path:cid>/pkmn/refresh')
def pkmn_refresh(cid):
 with conn() as c:link=c.execute('SELECT provider_id FROM pkmn_links WHERE card_id=?',(cid,)).fetchone()
 if not link:return err('Find and link the exact PkmnPrices card first')
 try:
  data,remaining=pkmn_request('/cards/'+link['provider_id'])
  prices=data.get('prices',[])
  # EUR Cardmarket NM only; don't silently convert USD into EUR.
  candidates=[p for p in prices if p.get('currency')=='EUR' and p.get('condition')=='Near Mint' and isinstance(p.get('market_price'),(int,float))]
  raw=candidates[0]['market_price'] if len(candidates)==1 else None
  if raw is not None:
   with conn() as c:
    c.execute('UPDATE cards SET raw=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(raw,cid))
    row=c.execute('SELECT raw,psa7,psa8,psa9,psa10 FROM cards WHERE id=?',(cid,)).fetchone()
    c.execute("INSERT INTO price_history(card_id,recorded_at,raw,psa7,psa8,psa9,psa10,source) VALUES(?,CURRENT_TIMESTAMP,?,?,?,?,?,'pkmnprices')",(cid,*row))
  return jsonify(ok=True,raw=raw,variants=prices,remaining=remaining,note='Raw EUR updated only for a single unambiguous Near Mint variant. Graded values unchanged; sold comps require paid API access.')
 except ValueError as e:return err(str(e),400)
 except Exception:return err('Provider temporarily unavailable',502)


@bp.post('/api/cards/<path:cid>/pkmn/grade')
def pkmn_grade(cid):
 d=request.get_json(silent=True) or {}
 grade=int(d.get('grade',0))
 if grade not in (7,8,9,10):return err('Grade must be 7, 8, 9 or 10')
 with conn() as c:link=c.execute('SELECT provider_id FROM pkmn_links WHERE card_id=?',(cid,)).fetchone()
 if not link:return err('Link a PkmnPrices card first')
 with conn() as c:cached=c.execute("SELECT sales FROM pkmn_sales_cache WHERE card_id=? AND grade=? AND fetched_at>=datetime('now','-24 hours')",(cid,grade)).fetchone()
 if cached:
  sales=json.loads(cached['sales'])
  return jsonify(sales=sales,comps=[x for x in sales if x.get('currency')=='EUR'],all_sales=sales,cached=True,note='No currency conversion; USD sales are displayed separately.')
 try:
  data,remaining=pkmn_request('/cards/'+link['provider_id']+'/listings/ebay',{'graded':'true','grader':'PSA','grade':str(grade),'period':'90d','limit':5})
  sales=latest_sales(data.get('data',[]),grade)
  with conn() as c:c.execute('INSERT INTO pkmn_sales_cache(card_id,grade,fetched_at,sales) VALUES(?,?,CURRENT_TIMESTAMP,?) ON CONFLICT(card_id,grade) DO UPDATE SET fetched_at=CURRENT_TIMESTAMP,sales=excluded.sales',(cid,grade,json.dumps(sales)))
  return jsonify(sales=sales,comps=[x for x in sales if str(x.get('currency','')).upper()=='EUR'],all_sales=sales,remaining=remaining,note='No currency conversion; USD sales are displayed separately. Nothing automatically overwrites stored PSA valuations.')
 except ValueError as e:return err(str(e),400)
 except Exception:return err('Provider temporarily unavailable',502)


@bp.post('/api/cards/<path:cid>/population')
def set_population(cid):
 d=request.get_json(force=True)
 with conn() as c:c.execute('INSERT INTO card_population(card_id,psa7,psa8,psa9,psa10,updated_at) VALUES(?,?,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(card_id) DO UPDATE SET psa7=excluded.psa7,psa8=excluded.psa8,psa9=excluded.psa9,psa10=excluded.psa10,updated_at=excluded.updated_at',(cid,*[int(d.get('psa'+str(i),0)) for i in (7,8,9,10)]))
 return jsonify(ok=True)


@bp.get('/api/cards/<path:cid>/population')
def get_population(cid):
 with conn() as c:r=c.execute('SELECT * FROM card_population WHERE card_id=?',(cid,)).fetchone()
 return jsonify(population=dict(r) if r else None)
