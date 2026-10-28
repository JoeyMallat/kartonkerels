from flask import Blueprint
from ..core import *

bp = Blueprint("accounts", __name__)

PHOTO_TYPES={'png':'image/png','jpg':'image/jpeg','webp':'image/webp'}

# Photos live in the database so they are replicated/backed up together with all other data.
with conn() as c:
 c.execute('CREATE TABLE IF NOT EXISTS upload_files(name TEXT PRIMARY KEY,mime TEXT NOT NULL,data BLOB NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP)')
 for _p in UPLOAD_DIR.iterdir():
  _ext=_p.suffix.lower().lstrip('.')
  if _p.is_file() and _ext in PHOTO_TYPES:
   c.execute('INSERT OR IGNORE INTO upload_files(name,mime,data) VALUES(?,?,?)',(_p.name,PHOTO_TYPES[_ext],_p.read_bytes()))

@bp.get('/api/auth/status')
def auth_status():
 with conn() as c:n=c.execute('SELECT COUNT(*) FROM members').fetchone()[0]
 return jsonify(bootstrapped=bool(n),user=me())


@bp.post('/api/auth/bootstrap')
def bootstrap():
 d=request.get_json(force=True)
 if not d.get('email') or not d.get('name') or len(d.get('password',''))<10:return err('Name, email and password (10+ characters) required')
 with conn() as c:
  if c.execute('SELECT COUNT(*) FROM members').fetchone()[0]:return err('Already initialized',403)
  cur=c.execute('INSERT INTO members(email,name,password_hash,role) VALUES(?,?,?,?)',(d['email'].strip().lower(),d['name'].strip(),generate_password_hash(d['password']),'admin'))
 session.permanent=True;session['member_id']=cur.lastrowid
 return jsonify(user=me()),201


_login_failures={};_login_lock=threading.Lock()
LOGIN_WINDOW=15*60;LOGIN_MAX_FAILURES=10

def _login_key():return (request.remote_addr or 'unknown')

def _recent_failures(key,now):
 return [t for t in _login_failures.get(key,[]) if now-t<LOGIN_WINDOW]


@bp.post('/api/auth/login')
def login():
 d=request.get_json(force=True);key=_login_key();now=time.time()
 with _login_lock:
  recent=_recent_failures(key,now);_login_failures[key]=recent
  if len(recent)>=LOGIN_MAX_FAILURES:return err('Too many failed login attempts. Try again in 15 minutes.',429)
 with conn() as c:r=c.execute('SELECT * FROM members WHERE email=?',(d.get('email','').strip().lower(),)).fetchone()
 if not r or not r['active'] or not check_password_hash(r['password_hash'],d.get('password','')):
  with _login_lock:_login_failures.setdefault(key,[]).append(now)
  return err('Invalid email or password',401)
 with _login_lock:_login_failures.pop(key,None)
 session.clear();session.permanent=True;session['member_id']=r['id'];return jsonify(user=me())


@bp.post('/api/auth/logout')
def logout():session.clear();return jsonify(ok=True)


@bp.patch('/api/auth/profile')
def profile():
 d=request.get_json(force=True);u=me()
 if 'name' in d:
  if not str(d['name']).strip():return err('Name required')
  with conn() as c:c.execute('UPDATE members SET name=? WHERE id=?',(str(d['name']).strip(),u['id']))
 if d.get('password'):
  if len(d['password'])<10:return err('Password must have 10+ characters')
  with conn() as c:c.execute('UPDATE members SET password_hash=? WHERE id=?',(generate_password_hash(d['password']),u['id']))
 return jsonify(user=me())


@bp.post('/api/auth/photo')
def upload_photo():
 f=request.files.get('photo')
 if not f or f.mimetype not in ('image/png','image/jpeg','image/webp'):return err('Upload a PNG, JPG or WebP')
 b=f.read(2_000_001)
 if len(b)>2_000_000:return err('Maximum photo size is 2 MB')
 ext={'image/png':'png','image/jpeg':'jpg','image/webp':'webp'}[f.mimetype]
 name=str(uuid.uuid4())+'.'+ext
 with conn() as c:
  c.execute('INSERT INTO upload_files(name,mime,data) VALUES(?,?,?)',(name,f.mimetype,b))
  c.execute('UPDATE members SET photo=? WHERE id=?',(name,me()['id']))
 return jsonify(user=me())


@bp.get('/uploads/<name>')
def photo_file(name):
 if not me():return err('Authentication required',401)
 name=secure_filename(name)
 with conn() as c:r=c.execute('SELECT mime,data FROM upload_files WHERE name=?',(name,)).fetchone()
 if r:return app.response_class(bytes(r['data']),mimetype=r['mime'],headers={'Cache-Control':'private, max-age=86400'})
 return send_from_directory(str(UPLOAD_DIR),name)


@bp.get('/api/members')
def members():
 with conn() as c:rows=c.execute('SELECT id,name,email,photo,role,active,joined_at FROM members ORDER BY active DESC,name').fetchall()
 return jsonify(members=[dict(x) for x in rows])


@bp.post('/api/members')
def create_member():
 if not admin():return err('Admin only',403)
 d=request.get_json(force=True)
 if not d.get('name') or not d.get('email') or len(d.get('password',''))<10:return err('Name, email, and initial password (10+ characters) required')
 try:
  with conn() as c:cur=c.execute('INSERT INTO members(name,email,password_hash) VALUES(?,?,?)',(d['name'].strip(),d['email'].strip().lower(),generate_password_hash(d['password'])))
 except sqlite3.IntegrityError:return err('Email already registered',409)
 return jsonify(id=cur.lastrowid),201


@bp.patch('/api/members/<int:mid>')
def member_update(mid):
 if not admin():return err('Admin only',403)
 d=request.get_json(force=True);fields={k:d[k] for k in ('active','name') if k in d}
 if not fields:return err('No fields')
 if mid==me()['id'] and fields.get('active')==0:return err('Cannot deactivate yourself')
 with conn() as c:c.execute('UPDATE members SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',[*fields.values(),mid])
 return jsonify(ok=True)


@bp.get('/api/config')
def config():
 return jsonify(grading_cost=float(setting('grading_cost','30')),selling_fee=float(setting('selling_fee','12')),currency=setting('currency','EUR'),pkmn_key_set=bool(setting('pkmn_api_key') or os.getenv('PKMNPRICES_API_KEY')),hci_key_set=bool(setting('hci_api_key') or HCI),hci_key_masked='••••••••' if setting('hci_api_key') else '',admin=admin())


@bp.patch('/api/config')
def config_update():
 if not admin():return err('Admin only',403)
 d=request.get_json(force=True)
 with conn() as c:
  for k in ('grading_cost','selling_fee','currency','hci_api_key','pkmn_api_key'):
   if k in d:c.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(k,str(d[k])))
 return jsonify(ok=True)

