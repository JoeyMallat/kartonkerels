from flask import Blueprint
from ..core import *

bp = Blueprint("catalog", __name__)

@bp.get('/')
def home():return send_from_directory(app.static_folder,'index.html')


@bp.get('/api/cards')
def cards():
 q=request.args.get('q','');s=request.args.get('set','');limit=min(max(int(request.args.get('limit',100)),1),500);page=max(int(request.args.get('page',1)),1)
 where=' WHERE (name LIKE ? OR set_name LIKE ? OR number LIKE ?)' ;params=['%'+q+'%']*3
 if s:where+=' AND set_name=?';params.append(s)
 with conn() as c:
  count=c.execute('SELECT COUNT(*) FROM cards'+where,params).fetchone()[0]
  rows=c.execute('SELECT * FROM cards'+where+' ORDER BY name,set_name,number LIMIT ? OFFSET ?',params+[limit,(page-1)*limit]).fetchall()
 return jsonify(cards=[dict(x) for x in rows],total=count,page=page)


@bp.get('/api/cards/<path:cid>')
def card_detail(cid):
 with conn() as c:
  card=c.execute('SELECT * FROM cards WHERE id=?',(cid,)).fetchone()
  if not card:return err('Card not found',404)
  inv=c.execute('SELECT * FROM inventory WHERE card_id=? ORDER BY id DESC',(cid,)).fetchall()
  history=c.execute('SELECT recorded_at,raw,psa7,psa8,psa9,psa10,source FROM price_history WHERE card_id=? ORDER BY recorded_at,id',(cid,)).fetchall()
  statuses=c.execute('SELECT status,COUNT(*) count FROM inventory WHERE card_id=? GROUP BY status',(cid,)).fetchall()
 return jsonify(card=dict(card),inventory=[dict(x) for x in inv],history=[dict(x) for x in history],statuses=[dict(x) for x in statuses],inventory_count=len(inv),unsold_count=sum(x['status']!='Sold' for x in inv))


@bp.get('/api/sets')
def sets():
 with conn() as c:rows=c.execute('SELECT set_name,COUNT(*) count FROM cards GROUP BY set_name ORDER BY set_name').fetchall()
 return jsonify(sets=[dict(x) for x in rows])


DIGITAL_SERIES=('tcgp',)

def digital_set_ids():
 ids=set()
 for serie in DIGITAL_SERIES:ids.update(x['id'] for x in geturl('https://api.tcgdex.net/v2/en/series/'+serie).get('sets',[]))
 return ids

def purge_digital_cards(set_ids):
 removed=0
 with conn() as c:
  for sid in set_ids:
   ids=[r[0] for r in c.execute("SELECT id FROM cards WHERE id LIKE ? ESCAPE '\\' AND id NOT IN (SELECT card_id FROM inventory)",(sid.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'-%',))]
   for table in ('price_history','pkmn_links','auto_price_status','pkmn_sales_cache','watchlist','alerts','card_population'):
    try:c.executemany(f'DELETE FROM {table} WHERE card_id=?',[(i,) for i in ids])
    except sqlite3.OperationalError:pass
   c.executemany('DELETE FROM cards WHERE id=?',[(i,) for i in ids]);removed+=len(ids)
 return removed


@bp.post('/api/import/tcgdex')
def import_cards():
 # TCGdex public catalog: import all physical sets and card summaries. Digital-only TCG Pocket sets are skipped.
 def work():
  state.update(status='running',count=0,error=None)
  try:
   digital=digital_set_ids()
   purge_digital_cards(digital)
   sets=[s for s in geturl('https://api.tcgdex.net/v2/en/sets') if s['id'] not in digital]
   for s in sets:
    data=geturl('https://api.tcgdex.net/v2/en/sets/'+s['id'])
    if (data.get('serie') or {}).get('id') in DIGITAL_SERIES:continue
    entries=[(x['id'],x.get('name','Unknown'),data.get('name',s.get('name','')),x.get('localId',''),x.get('image')) for x in data.get('cards',[])]
    with conn() as c:c.executemany('INSERT INTO cards(id,name,set_name,number,image) VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,set_name=excluded.set_name,number=excluded.number,image=excluded.image',entries)
    state['count']+=len(entries)
    time.sleep(.12)
   state['status']='completed'
  except Exception as e:state.update(status='failed',error=str(e))
 if state['status']=='running':return err('Import already running',409)
 threading.Thread(target=work,daemon=True).start();return jsonify(status='started'),202


@bp.get('/api/import/status')
def import_status():return jsonify(state)


@bp.post('/api/cards/<path:cid>/price')
def manual_price(cid):
 return err('Manual price editing is disabled. Prices are provider-managed.',403)


@bp.post('/api/cards/<path:cid>/hci')
def hci(cid):
 key=setting('hci_api_key') or HCI
 if not key:return err('Set HobbyCardIndex API key in Settings first',503)
 with conn() as c:
  card=c.execute('SELECT * FROM cards WHERE id=?',(cid,)).fetchone()
  if not card:return err('Card not found',404)
 try:
  from urllib.parse import quote
  data=geturl('https://hobbycardindex.com/api/v1/prices/search?q='+quote(card['name']+' '+card['set_name']+' '+(card['number'] or ''))+'&category=pokemon',{'X-API-Key':key,'Accept':'application/json'})
  return jsonify(results=data,notice='Review exact set, number and variant before assigning prices; no automatic matching is performed.')
 except Exception as e:return err('HCI lookup failed: '+str(e),502)

