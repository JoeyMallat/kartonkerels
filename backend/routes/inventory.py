from flask import Blueprint
from ..core import *

bp = Blueprint("inventory", __name__)

@bp.get('/api/inventory')
def inventory():
 with conn() as c:rows=c.execute('SELECT i.*,c.name,c.set_name,c.number,c.image,c.raw,c.psa7,c.psa8,c.psa9,c.psa10,c.population,c.updated_at FROM inventory i JOIN cards c ON c.id=i.card_id ORDER BY i.id DESC').fetchall()
 return jsonify(items=[dict(x) for x in rows])


@bp.post('/api/inventory')
def add():
 d=request.get_json(force=True);cid=d.get('card_id');paid=d.get('paid')
 if not cid or paid is None:return err('card_id and paid are required')
 with conn() as c:
  if not c.execute('SELECT 1 FROM cards WHERE id=?',(cid,)).fetchone():return err('Card not found',404)
  cur=c.execute('INSERT INTO inventory(card_id,paid,grading_cost,owner,notes) VALUES(?,?,?,?,?)',(cid,float(paid),float(d.get('grading_cost',30)),d.get('owner',''),d.get('notes','')))
  return jsonify(id=cur.lastrowid),201


@bp.patch('/api/inventory/<int:id>')
def update(id):
 d=request.get_json(force=True);allowed={'paid','grading_cost','other_cost','grade','status','list_price','sold_price','owner','notes'};fields={k:v for k,v in d.items() if k in allowed}
 if not fields:return err('No valid fields')
 if 'status' in fields and fields['status'] not in STATUSES:return err('Invalid status')
 if 'grade' in fields and fields['grade'] not in (None,7,8,9,10):return err('Grade must be 7–10')
 with conn() as c:
  cur=c.execute('UPDATE inventory SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',list(fields.values())+[id]);return jsonify(ok=bool(cur.rowcount))


@bp.delete('/api/inventory/<int:id>')
def delete(id):
 with conn() as c:c.execute('DELETE FROM inventory WHERE id=?',(id,))
 return jsonify(ok=True)


@bp.get('/api/summary')
def summary():
 with conn() as c:
  rows=c.execute('SELECT i.*,c.raw,c.psa7,c.psa8,c.psa9,c.psa10 FROM inventory i JOIN cards c ON c.id=i.card_id').fetchall()
 fee=float(request.args.get('fee',12))/100; invested=realized=unrealized=market=0
 for r in rows:
  cost=r['paid']+r['grading_cost']+r['other_cost'];invested+=cost
  if r['status']=='Sold' and r['sold_price'] is not None:realized+=r['sold_price']*(1-fee)-cost
  else:
   price=r['psa'+str(r['grade'])] if r['grade'] else r['raw'];price=price or 0;market+=price;unrealized+=price*(1-fee)-cost
 return jsonify(count=len(rows),invested=round(invested,2),market=round(market,2),unrealized=round(unrealized,2),realized=round(realized,2))


@bp.post('/api/v2/inventory')
def v2_add():
 d=request.get_json(force=True);u=me();mid=u['id']
 if d.get('for_member'):
  mid=int(d.get('member_id',0))
  with conn() as c:
   if not c.execute('SELECT 1 FROM members WHERE id=? AND active=1',(mid,)).fetchone():return err('Select a valid team member')
 cid=d.get('card_id')
 try:paid=float(d['paid'])
 except (ValueError,KeyError,TypeError):return err('Valid purchase price required')
 if paid<0:return err('Price cannot be negative')
 with conn() as c:
  if not c.execute('SELECT 1 FROM cards WHERE id=?',(cid,)).fetchone():return err('Card not found',404)
  cur=c.execute('INSERT INTO inventory(card_id,paid,grading_cost,member_id,owner,status,notes,purchased_at) VALUES(?,?,?,?,?,?,?,CURRENT_TIMESTAMP)',(cid,paid,float(d.get('grading_cost',setting('grading_cost','30'))),mid,str(mid),'Purchased',d.get('notes','')))
 activity('purchase',f'Purchased {cid} for €{paid:.2f}',mid)
 return jsonify(id=cur.lastrowid),201


@bp.patch('/api/v2/inventory/<int:iid>')
def v2_inventory_update(iid):
 d=request.get_json(force=True)
 allowed={'paid','grading_cost','other_cost','grade','expected_grade','status','list_price','sold_price','notes','batch_id','platform','listing_url','selling_fee','suggested_price','member_id'}
 f={k:v for k,v in d.items() if k in allowed}
 if not f:return err('No changes')
 if 'status' in f and f['status'] not in STATUSES:return err('Invalid status')
 for k in ('grade','expected_grade'):
  if k in f and f[k] not in (None,7,8,9,10):return err('Grade must be 7–10')
 if 'member_id' in f:
  with conn() as c:
   if not c.execute('SELECT 1 FROM members WHERE id=? AND active=1',(f['member_id'],)).fetchone():return err('Invalid team member')
 with conn() as c:
  before=c.execute('SELECT * FROM inventory WHERE id=?',(iid,)).fetchone()
  if not before:return err('Not found',404)
  if f.get('status')=='Sold' and f.get('sold_price',before['sold_price']) is None:return err('Sale price required to mark sold')
  if f.get('status')=='Sold':f['sold_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
  if f.get('status')=='Listed':f['listed_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
  c.execute('UPDATE inventory SET '+','.join(k+'=?' for k in f)+' WHERE id=?',[*f.values(),iid])
 activity('inventory',f'Updated inventory item #{iid}')
 return jsonify(ok=True)


@bp.get('/api/v2/inventory')
def v2_inventory():
 with conn() as c:rows=c.execute('''SELECT i.*,c.name,c.set_name,c.number,c.image,c.raw,c.psa7,c.psa8,c.psa9,c.psa10,c.population,m.name member_name,m.photo member_photo,b.name batch_name FROM inventory i JOIN cards c ON c.id=i.card_id LEFT JOIN members m ON m.id=i.member_id LEFT JOIN batches b ON b.id=i.batch_id ORDER BY i.id DESC''').fetchall()
 return jsonify(items=[dict(x) for x in rows])


@bp.get('/api/export/inventory.csv')
def export_inventory():
 import csv,io
 with conn() as c:rows=c.execute('SELECT i.*,c.name,c.set_name,c.number FROM inventory i JOIN cards c ON c.id=i.card_id ORDER BY i.id').fetchall()
 out=io.StringIO()
 if rows:
  w=csv.DictWriter(out,fieldnames=list(dict(rows[0]).keys()));w.writeheader();w.writerows([dict(x) for x in rows])
 return app.response_class(out.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename=kartonkerels-inventory.csv'})

