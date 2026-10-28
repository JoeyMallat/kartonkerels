from flask import Blueprint
from ..core import *

bp = Blueprint("operations", __name__)

@bp.get('/api/batches')
def batches():
 with conn() as c:
  rows=c.execute('SELECT b.*,(SELECT COUNT(*) FROM inventory i WHERE i.batch_id=b.id) cards FROM batches b ORDER BY b.id DESC').fetchall()
 return jsonify(batches=[dict(x) for x in rows])


@bp.post('/api/batches')
def create_batch():
 d=request.get_json(force=True)
 if not d.get('name'):return err('Batch name required')
 with conn() as c:cur=c.execute('INSERT INTO batches(name,reference,submitted_at,expected_at,shipping,insurance,notes) VALUES(?,?,?,?,?,?,?)',(d['name'],d.get('reference'),d.get('submitted_at'),d.get('expected_at'),float(d.get('shipping') or 0),float(d.get('insurance') or 0),d.get('notes')))
 activity('batch',f'Created PSA batch {d["name"]}')
 return jsonify(id=cur.lastrowid),201


@bp.patch('/api/batches/<int:bid>')
def update_batch(bid):
 d=request.get_json(force=True);allowed={'name','reference','submitted_at','expected_at','returned_at','shipping','insurance','notes'};f={k:v for k,v in d.items() if k in allowed}
 if not f:return err('No changes')
 with conn() as c:c.execute('UPDATE batches SET '+','.join(k+'=?' for k in f)+' WHERE id=?',[*f.values(),bid])
 return jsonify(ok=True)


@bp.get('/api/finance')
def finance():
 with conn() as c:
  cash=[dict(x) for x in c.execute('SELECT e.*,m.name member_name FROM cash_entries e LEFT JOIN members m ON m.id=e.member_id ORDER BY e.id DESC').fetchall()]
  expenses=[dict(x) for x in c.execute('SELECT e.*,m.name member_name FROM expenses e LEFT JOIN members m ON m.id=e.member_id ORDER BY e.id DESC').fetchall()]
 return jsonify(cash=cash,expenses=expenses)


@bp.post('/api/finance/<kind>')
def add_finance(kind):
 if kind not in ('cash','expense'):return err('Invalid type')
 d=request.get_json(force=True)
 try:amount=float(d['amount'])
 except (ValueError,KeyError,TypeError):return err('Amount required')
 if amount<=0:return err('Amount must be positive')
 with conn() as c:
  if kind=='cash':c.execute('INSERT INTO cash_entries(amount,description,member_id) VALUES(?,?,?)',(amount,d.get('description'),me()['id']))
  else:c.execute('INSERT INTO expenses(amount,category,description,member_id) VALUES(?,?,?,?)',(amount,d.get('category','Other'),d.get('description'),me()['id']))
 activity(kind,f'{kind}: €{amount:.2f}')
 return jsonify(ok=True),201


@bp.get('/api/watchlist')
def get_watchlist():
 with conn() as c:rows=c.execute('SELECT w.card_id,c.name,c.set_name,c.raw,c.psa9,c.psa10 FROM watchlist w JOIN cards c ON c.id=w.card_id WHERE w.member_id=?',(me()['id'],)).fetchall()
 return jsonify(cards=[dict(x) for x in rows])


@bp.post('/api/watchlist/<path:cid>')
def watch_add(cid):
 with conn() as c:c.execute('INSERT OR IGNORE INTO watchlist(member_id,card_id) VALUES(?,?)',(me()['id'],cid))
 return jsonify(ok=True)


@bp.delete('/api/watchlist/<path:cid>')
def watch_del(cid):
 with conn() as c:c.execute('DELETE FROM watchlist WHERE member_id=? AND card_id=?',(me()['id'],cid))
 return jsonify(ok=True)


@bp.get('/api/alerts')
def get_alerts():
 with conn() as c:rows=c.execute('SELECT a.*,c.name,c.raw,c.psa7,c.psa8,c.psa9,c.psa10 FROM alerts a JOIN cards c ON c.id=a.card_id WHERE a.member_id=?',(me()['id'],)).fetchall()
 return jsonify(alerts=[dict(x) for x in rows])


@bp.post('/api/alerts')
def add_alert():
 d=request.get_json(force=True)
 if d.get('grade') not in (7,8,9,10):return err('Grade must be 7–10')
 with conn() as c:c.execute('INSERT INTO alerts(member_id,card_id,grade,target_profit) VALUES(?,?,?,?)',(me()['id'],d['card_id'],d['grade'],float(d['target_profit'])))
 return jsonify(ok=True),201


@bp.delete('/api/alerts/<int:aid>')
def del_alert(aid):
 with conn() as c:c.execute('DELETE FROM alerts WHERE id=? AND member_id=?',(aid,me()['id']))
 return jsonify(ok=True)


@bp.get('/api/team/stats')
def team_stats():
 with conn() as c:
  members=[dict(x) for x in c.execute('SELECT id,name,email,photo,joined_at FROM members WHERE active=1 ORDER BY name')]
  items=[dict(x) for x in c.execute('SELECT * FROM inventory')]
  activity_rows=[dict(x) for x in c.execute('SELECT a.*,m.name member_name FROM activity a LEFT JOIN members m ON m.id=a.member_id ORDER BY a.id DESC LIMIT 30')]
 fee=float(setting('selling_fee','12'))/100
 now=datetime.datetime.now(datetime.timezone.utc);month=now.strftime('%Y-%m')
 for m in members:
  own=[x for x in items if x['member_id']==m['id']];sold=[x for x in own if x['status']=='Sold' and x['sold_price'] is not None]
  for x in sold:
   x['_profit']=x['sold_price']*(1-(x['selling_fee']/100 if x['selling_fee'] is not None else fee))-x['paid']-(x['grading_cost'] or 0)-(x['other_cost'] or 0)
  m.update(sold=len(sold),inventory=len([x for x in own if x['status']!='Sold']),profit=round(sum(x['_profit'] for x in sold),2),monthly_profit=round(sum(x['_profit'] for x in sold if (x['sold_at'] or x['created_at'] or '').startswith(month)),2),roi=round(100*sum(x['_profit'] for x in sold)/sum(x['paid']+(x['grading_cost'] or 0)+(x['other_cost'] or 0) for x in sold),1) if sold and sum(x['paid']+(x['grading_cost'] or 0)+(x['other_cost'] or 0) for x in sold)>0 else 0,gem_rate=round(100*len([x for x in own if x['grade']==10])/len([x for x in own if x['grade'] is not None]),1) if any(x['grade'] is not None for x in own) else None,biggest_flip=round(max([x['_profit'] for x in sold],default=0),2))
 months=[]
 for i in range(11,-1,-1):
  y=now.year+(now.month-i-1)//12;mo=(now.month-i-1)%12+1;key=f'{y:04d}-{mo:02d}'
  entry={'month':key,'total':0}
  for m in members:
   value=0
   for x in items:
    if x.get('member_id')==m['id'] and x.get('status')=='Sold' and x.get('sold_price') is not None and (x.get('sold_at') or x.get('created_at') or '').startswith(key):value+=x['sold_price']*(1-(x['selling_fee']/100 if x['selling_fee'] is not None else fee))-x['paid']-(x['grading_cost'] or 0)-(x['other_cost'] or 0)
   entry[str(m['id'])]=round(value,2);entry['total']+=value
  entry['total']=round(entry['total'],2);months.append(entry)
 return jsonify(members=members,months=months,activity=activity_rows,king=max(members,key=lambda x:x['monthly_profit']) if members and any(x['monthly_profit']>0 for x in members) else None)


@bp.get('/api/analytics')
def analytics():
 with conn() as c:
  items=[dict(x) for x in c.execute('SELECT i.*,c.raw,c.psa7,c.psa8,c.psa9,c.psa10 FROM inventory i JOIN cards c ON c.id=i.card_id')]
  cash=c.execute('SELECT COALESCE(SUM(amount),0) FROM cash_entries').fetchone()[0]
  expenses=c.execute('SELECT COALESCE(SUM(amount),0) FROM expenses').fetchone()[0]
  market_count=c.execute('SELECT COUNT(*) FROM cards').fetchone()[0]
  priced_count=c.execute('SELECT COUNT(*) FROM cards WHERE raw IS NOT NULL AND psa10 IS NOT NULL').fetchone()[0]
 fee=float(setting('selling_fee','12'))/100;cash_balance=cash-expenses;realized=0;unrealized=0;inventory_value=0;invested=0;aging={'30':0,'60':0,'90':0};now=datetime.datetime.now(datetime.timezone.utc)
 for x in items:
  cost=x['paid']+(x['grading_cost'] or 0)+(x['other_cost'] or 0);invested+=cost
  if x['status']=='Sold' and x['sold_price'] is not None:
   realized+=x['sold_price']*(1-(x['selling_fee']/100 if x['selling_fee'] is not None else fee))-cost;cash_balance+=x['sold_price']*(1-(x['selling_fee']/100 if x['selling_fee'] is not None else fee))
  else:
   cash_balance-=cost
   market=x.get('psa'+str(x['grade'])) if x['grade'] else x['raw'];market=market or 0
   inventory_value+=market;unrealized+=market*(1-fee)-cost
   try:days=(now-datetime.datetime.fromisoformat((x['purchased_at'] or x['created_at']).replace('Z','+00:00')).replace(tzinfo=datetime.timezone.utc)).days
   except Exception:days=0
   for k in aging:
    if days>=int(k):aging[k]+=1
 return jsonify(cash=round(cash_balance,2),invested=round(invested,2),realized=round(realized-expenses,2),unrealized=round(unrealized,2),market_value=round(inventory_value,2),net_worth=round(cash_balance+inventory_value*(1-fee),2),cards=len(items),catalog=market_count,priced=priced_count,aging=aging)

