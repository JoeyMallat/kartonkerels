from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
import os,sqlite3,json,urllib.request,datetime,threading,time,secrets,hashlib,hmac,base64,uuid,calendar,collections
from functools import wraps
from werkzeug.security import generate_password_hash,check_password_hash
from werkzeug.utils import secure_filename
from flask import session,send_file
from flask import Flask,request,jsonify,send_from_directory
app=Flask(__name__,static_folder=str(PROJECT_ROOT / 'static'))
DATA_DIR=Path(os.getenv('DATA_DIR',str(PROJECT_ROOT))).resolve();DATA_DIR.mkdir(parents=True,exist_ok=True)
DB=os.getenv('DATABASE_PATH',str(DATA_DIR / 'kartonkerels.db'));UPLOAD_DIR=DATA_DIR / 'uploads';UPLOAD_DIR.mkdir(exist_ok=True);HCI=os.getenv('HCI_API_KEY','')
def _secret_key():
 # A stable key keeps team members logged in across restarts; generated once and stored beside the database.
 if os.getenv('SECRET_KEY'):return os.getenv('SECRET_KEY')
 path=DATA_DIR / '.secret_key'
 if not path.exists():path.write_text(secrets.token_hex(32),encoding='utf-8')
 return path.read_text(encoding='utf-8').strip()
def _env_flag(name):return os.getenv(name,'').strip().lower() in ('1','true','yes','on')
app.secret_key=_secret_key()
app.config.update(SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Lax',SESSION_COOKIE_SECURE=_env_flag('SECURE_COOKIES'),PERMANENT_SESSION_LIFETIME=datetime.timedelta(days=14),MAX_CONTENT_LENGTH=5*1024*1024)
if _env_flag('TRUST_PROXY'):
 # Only enable behind a reverse proxy/tunnel (e.g. cloudflared), otherwise clients could spoof their IP.
 from werkzeug.middleware.proxy_fix import ProxyFix
 app.wsgi_app=ProxyFix(app.wsgi_app,x_for=1,x_proto=1,x_host=1)
@app.after_request
def security_headers(r):
 r.headers.setdefault('X-Content-Type-Options','nosniff');r.headers.setdefault('X-Frame-Options','DENY');r.headers.setdefault('Referrer-Policy','same-origin')
 return r
def conn():
 c=sqlite3.connect(DB,timeout=20);c.row_factory=sqlite3.Row;return c
def init():
 with conn() as c:
  # WAL lets team members read while someone else is writing.
  c.execute('PRAGMA journal_mode=WAL')
  c.executescript('''CREATE TABLE IF NOT EXISTS cards(id TEXT PRIMARY KEY,name TEXT NOT NULL,set_name TEXT,number TEXT,image TEXT,raw REAL,psa7 REAL,psa8 REAL,psa9 REAL,psa10 REAL,hci_id TEXT,population TEXT,updated_at TEXT);CREATE INDEX IF NOT EXISTS cards_name ON cards(name);CREATE INDEX IF NOT EXISTS cards_set ON cards(set_name);CREATE TABLE IF NOT EXISTS inventory(id INTEGER PRIMARY KEY AUTOINCREMENT,card_id TEXT NOT NULL,paid REAL NOT NULL,grading_cost REAL DEFAULT 30,other_cost REAL DEFAULT 0,grade INTEGER,status TEXT NOT NULL DEFAULT 'Purchased',list_price REAL,sold_price REAL,owner TEXT,notes TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP,FOREIGN KEY(card_id) REFERENCES cards(id));CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);''')
  if not c.execute('SELECT COUNT(*) FROM cards').fetchone()[0]:
   demo=[('base1-4','Charizard','Base Set','4/102',None,None,None,None,None,None),('sv3pt5-199','Charizard ex','151','199/165',None,None,None,None,None,None),('swsh7-215','Umbreon VMAX','Evolving Skies','215/203',None,None,None,None,None,None),('sv8-238','Pikachu ex','Surging Sparks','238/191',None,None,None,None,None,None),('swsh11-186','Giratina V','Lost Origin','186/196',None,None,None,None,None,None)]
   c.executemany('INSERT INTO cards(id,name,set_name,number,image,raw,psa7,psa8,psa9,psa10) VALUES(?,?,?,?,?,?,?,?,?,?)',demo)
init()
with conn() as c:
 c.execute('''CREATE TABLE IF NOT EXISTS price_history(id INTEGER PRIMARY KEY AUTOINCREMENT,card_id TEXT NOT NULL,recorded_at TEXT NOT NULL,raw REAL,psa7 REAL,psa8 REAL,psa9 REAL,psa10 REAL,source TEXT DEFAULT 'manual',FOREIGN KEY(card_id) REFERENCES cards(id))''')
 c.execute('CREATE INDEX IF NOT EXISTS price_history_card_date ON price_history(card_id,recorded_at)')
 c.execute('''INSERT INTO price_history(card_id,recorded_at,raw,psa7,psa8,psa9,psa10,source) SELECT id,COALESCE(updated_at,CURRENT_TIMESTAMP),raw,psa7,psa8,psa9,psa10,'initial' FROM cards WHERE (raw IS NOT NULL OR psa7 IS NOT NULL OR psa8 IS NOT NULL OR psa9 IS NOT NULL OR psa10 IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM price_history h WHERE h.card_id=cards.id)''')
def geturl(url,headers=None):
 req=urllib.request.Request(url,headers=headers or {'User-Agent':'Kartonkerels/1.0'})
 with urllib.request.urlopen(req,timeout=30) as r:return json.load(r)
def err(message,status=400):return jsonify(error=message),status
STATUSES=['Purchased','On way to PSA','Inventory','Listed','Sold']
state={'status':'idle','count':0,'error':None}
import urllib.error
from urllib.parse import urlencode
PKMN_BASE='https://api.pkmnprices.com/v1'
with conn() as c:
 c.execute('CREATE TABLE IF NOT EXISTS pkmn_links(card_id TEXT PRIMARY KEY,provider_id TEXT NOT NULL)')
 c.execute('CREATE TABLE IF NOT EXISTS pkmn_usage(id INTEGER PRIMARY KEY AUTOINCREMENT,used_at TEXT DEFAULT CURRENT_TIMESTAMP,credits INTEGER NOT NULL DEFAULT 0,endpoint TEXT NOT NULL)')
with conn() as c:
 c.execute('CREATE TABLE IF NOT EXISTS auto_price_status(card_id TEXT PRIMARY KEY,checked_at TEXT,message TEXT)')
def pkmn_request(path,params=None):
 key=setting('pkmn_api_key') or os.getenv('PKMNPRICES_API_KEY','')
 if not key:raise ValueError('Enter your PkmnPrices API key in Settings')
 url=PKMN_BASE+path+('?' + urlencode(params) if params else '')
 req=urllib.request.Request(url,headers={'X-API-Key':key,'Accept':'application/json','User-Agent':'Kartonkerels/2'})
 try:
  with urllib.request.urlopen(req,timeout=20) as r:
   result=json.load(r)
   charged=int(r.headers.get('x-credits-charged') or (max(1,len(result.get('data',[]))) if isinstance(result,dict) and isinstance(result.get('data'),list) else 1))
   remaining=r.headers.get('x-credits-remaining')
 except urllib.error.HTTPError as e:
  if e.code in (401,403):raise ValueError('PkmnPrices rejected the key or this endpoint requires a higher plan')
  if e.code==429:raise ValueError('PkmnPrices API limit reached')
  raise ValueError('PkmnPrices returned HTTP '+str(e.code))
 with conn() as c:c.execute('INSERT INTO pkmn_usage(credits,endpoint) VALUES(?,?)',(charged,path))
 return result,remaining
import difflib, statistics, re, unicodedata
with conn() as c:
 if 'price_currency' not in [x['name'] for x in c.execute('PRAGMA table_info(cards)').fetchall()]:
  c.execute('ALTER TABLE cards ADD COLUMN price_currency TEXT')
with conn() as c:
 for card_id,raw in [('base1-4',310),('sv3pt5-199',95),('swsh7-215',950),('sv8-238',185),('swsh11-186',340)]:
  c.execute('UPDATE cards SET raw=NULL,psa7=NULL,psa8=NULL,psa9=NULL,psa10=NULL WHERE id=? AND raw=? AND price_currency IS NULL',(card_id,raw))
def normalize_card(s):
 s=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode()
 return re.sub(r'[^a-z0-9]','',s.lower())
def base_card_name(s):
 # Provider names often append printing details: "Acerola (Full Art)", "Name - 2010 (Player)".
 return normalize_card(re.split(r'\s+[-–]\s+',re.sub(r'\s*[\(\[].*?[\)\]]','',str(s or '')))[0])
def name_score(provider,ours):
 if normalize_card(provider)==normalize_card(ours):return 1.0
 a,b=base_card_name(provider),normalize_card(ours)
 if a==b:return .95
 if a and b and (a in b or b in a):return .85
 return difflib.SequenceMatcher(None,a,b).ratio()
def set_score(provider,ours):
 a,b=normalize_card(provider),normalize_card(ours)
 if a==b:return 1.0
 if a and b and (b in a or a in b):return .95
 return difflib.SequenceMatcher(None,a,b).ratio()
def pick_match(card, options):
 number=str(card['number'] or '').split('/')[0].lstrip('0') or '0'
 total=str(card['number'] or '').split('/')[-1].lstrip('0') if '/' in str(card['number'] or '') else ''
 candidates=[]
 for x in options:
  if str(x.get('number','')).split('/')[0].lstrip('0').upper()!=number.upper():continue
  if total and x.get('total_set_number') and str(x['total_set_number']).lstrip('0')!=total:continue
  ns=name_score(x.get('name'),card['name'])
  if ns<.7:continue
  ss=set_score((x.get('set') or {}).get('name',''),card['set_name'])
  if ss<.6:continue
  candidates.append((ss*2+ns,x))
 if not candidates:return None
 candidates.sort(key=lambda t:t[0],reverse=True)
 return str(candidates[0][1]['id'])
def extract_raw_prices(detail):
 prices=[x for x in (detail.get('prices') or []) if isinstance(x.get('market_price'),(int,float)) and x['market_price']>0]
 # Prefer USD Near Mint and the ordinary printing; fall back to other conditions so a raw price is always shown.
 variants={'normal':0,'holofoil':1,'unlimited':2,'':3,'1st edition':4,'reverse holofoil':9,'reverse holo':9}
 conditions={'near mint':0,'lightly played':1,'moderately played':2,'heavily played':3,'damaged':4}
 currencies={'USD':0,'EUR':1}
 rows=[x for x in prices if str(x.get('currency','')).upper() in currencies]
 if not rows:return None,None,None
 rows.sort(key=lambda x:(conditions.get(str(x.get('condition','')).lower(),5),currencies[str(x['currency']).upper()],variants.get(str(x.get('variant') or '').lower(),5)))
 best=rows[0];label=str(best.get('variant') or '')
 if str(best.get('condition','')).lower()!='near mint':label=(label+', ' if label else '')+str(best.get('condition') or 'unknown condition')
 return float(best['market_price']),str(best['currency']).upper(),label
with conn() as c:
 c.executescript('''
 CREATE TABLE IF NOT EXISTS members(id INTEGER PRIMARY KEY AUTOINCREMENT,email TEXT UNIQUE NOT NULL,name TEXT NOT NULL,password_hash TEXT NOT NULL,photo TEXT,role TEXT NOT NULL DEFAULT 'member',active INTEGER NOT NULL DEFAULT 1,joined_at TEXT DEFAULT CURRENT_TIMESTAMP);
 CREATE TABLE IF NOT EXISTS expenses(id INTEGER PRIMARY KEY AUTOINCREMENT,amount REAL NOT NULL,category TEXT NOT NULL,description TEXT,member_id INTEGER,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
 CREATE TABLE IF NOT EXISTS cash_entries(id INTEGER PRIMARY KEY AUTOINCREMENT,amount REAL NOT NULL,description TEXT,member_id INTEGER,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
 CREATE TABLE IF NOT EXISTS batches(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,reference TEXT,submitted_at TEXT,expected_at TEXT,returned_at TEXT,shipping REAL DEFAULT 0,insurance REAL DEFAULT 0,notes TEXT);
 CREATE TABLE IF NOT EXISTS watchlist(member_id INTEGER NOT NULL,card_id TEXT NOT NULL,PRIMARY KEY(member_id,card_id));
 CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY AUTOINCREMENT,member_id INTEGER NOT NULL,card_id TEXT NOT NULL,grade INTEGER NOT NULL,target_profit REAL NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
 CREATE TABLE IF NOT EXISTS activity(id INTEGER PRIMARY KEY AUTOINCREMENT,member_id INTEGER,kind TEXT,detail TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
 CREATE TABLE IF NOT EXISTS card_population(card_id TEXT PRIMARY KEY,psa7 INTEGER,psa8 INTEGER,psa9 INTEGER,psa10 INTEGER,updated_at TEXT);
 ''')
 for col,kind in [('member_id','INTEGER'),('batch_id','INTEGER'),('expected_grade','INTEGER'),('listed_at','TEXT'),('sold_at','TEXT'),('platform','TEXT'),('listing_url','TEXT'),('selling_fee','REAL'),('suggested_price','REAL'),('purchased_at','TEXT')]:
  try:c.execute(f'ALTER TABLE inventory ADD COLUMN {col} {kind}')
  except sqlite3.OperationalError:pass
def setting(k,default=None):
 with conn() as c:
  r=c.execute('SELECT value FROM settings WHERE key=?',(k,)).fetchone()
 return r['value'] if r else default
def me():
 mid=session.get('member_id')
 if not mid:return None
 with conn() as c:r=c.execute('SELECT id,email,name,photo,role,active,joined_at FROM members WHERE id=?',(mid,)).fetchone()
 return dict(r) if r and r['active'] else None
@app.before_request
def guard():
 if request.path.startswith('/api/') and not request.path.startswith('/api/auth/') and not me():return err('Authentication required',401)
 if request.method in ('POST','PATCH','DELETE') and (request.path=='/api/inventory' or request.path.startswith('/api/inventory/')):return err('Use the authenticated v2 inventory API',410)
def admin():
 u=me()
 return bool(u and u['role']=='admin')
def activity(kind,detail,member_id=None):
 with conn() as c:c.execute('INSERT INTO activity(member_id,kind,detail) VALUES(?,?,?)',(member_id or session.get('member_id'),kind,detail))
