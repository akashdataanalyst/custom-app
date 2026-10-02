"""Targeted, idempotent FG Item creation from approved source; never posts stock."""
import json,re,unicodedata,hashlib,html
from pathlib import Path
from collections import Counter
import frappe
from openpyxl import load_workbook
PACKAGE=Path(__file__).with_name('fg_standard_corrected_20260915')
SHA='74dbe59b6271ffbff5a4c07d3e10d18836683095782eee4856d4f3f5eed6e706'
VERSION='fg-item-source-v1'
PROFILE={'item_group':'Finished Goods','stock_uom':'Kg','is_stock_item':1,'is_sales_item':1,'is_purchase_item':1,'has_batch_no':1,'create_new_batch':1,'has_serial_no':0,'valuation_method':'FIFO','include_item_in_manufacturing':1,'inspection_required_before_purchase':0,'inspection_required_before_delivery':1,'quality_inspection_template':'Calco Final FG QC','custom_calco_release_status':'Draft','allow_negative_stock':0}
REFERENCE='720C0002'
def normcode(value):return re.sub(r'[^A-Z0-9]','',unicodedata.normalize('NFKC',str(value or '')).upper())
def normname(value):return re.sub(r'[^a-z0-9]','',unicodedata.normalize('NFKC',str(value or '').replace('™','').replace('®','').replace('©','')).casefold())
def source_rows():
 manifest=json.loads((PACKAGE/'manifest.json').read_text());path=PACKAGE/manifest['file']
 if hashlib.sha256(path.read_bytes()).hexdigest()!=SHA:raise ValueError('Source SHA mismatch')
 scope=json.loads((PACKAGE/'missing_item_scope.json').read_text());w=load_workbook(path,read_only=True,data_only=False)
 try:
  rows=list(w['Sheet1'].values);headers={str(v).strip():i for i,v in enumerate(rows[0]) if v is not None};result=[]
  for r in scope:
   v=rows[int(r['row'])-1];assert str(v[headers['Code']]).strip()==r['fg']
   result.append({'fg':r['fg'],'row':int(r['row']),'item_name':v[headers['Product Name']],'status':str(v[headers['Material Status_1']] or '').strip()})
  assert len(result)==345 and len({r['fg'] for r in result})==345
  return result
 finally:w.close()
def profile():
 ref=frappe.get_doc('Item',REFERENCE)
 if any(ref.get(k)!=v for k,v in PROFILE.items()):raise ValueError('Reference FG convention changed; review required')
 defaults=[{'company':r.company,'default_warehouse':r.default_warehouse} for r in ref.item_defaults]
 if defaults!=[{'company':'Calco PolyTechnik Pvt Ltd','default_warehouse':'Stores - CPPL'}]:raise ValueError('Company FG defaults changed')
 return {**PROFILE,'item_defaults':defaults,'opening_stock':0,'standard_rate':0,'valuation_rate':0,'disabled':0,'custom_fg_grade_classification':''}
def classify(row,items,peers,aliases):
 code=row['fg'];name=normname(row['item_name']);issues=[]
 if any(x['name']==code for x in items):return {'category':'Existing','matches':[code],'issues':[]}
 if row['status'].casefold() in ('inactive','obsolete','superseded'):return {'category':'D','matches':[],'issues':['Inactive source']}
 if not re.fullmatch(r'[0-9]{3}[A-Z][A-Z0-9-]+',code) or not name or row['status'] not in ('Production','PreLaunch'):return {'category':'E','matches':[],'issues':['Insufficient code/name or ambiguous source status']}
 matches=[x['name'] for x in items if normcode(x['name'])==normcode(code) or (name and (normname(x['item_name'])==name or normname(x.get('description'))==name))]
 matches+=aliases.get(normcode(code),[])
 peer=[x['fg'] for x in peers if x['fg']!=code and (normcode(x['fg'])==normcode(code) or (name and normname(x['item_name'])==name))]
 if matches or peer:return {'category':'B','matches':sorted(set(matches)),'peer_matches':peer,'issues':['Possible existing/peer identity; no automatic merge or duplicate creation']}
 return {'category':'C' if row['status']=='PreLaunch' else 'A','matches':[],'issues':[]}
def run(dry_run=True,expected_site=None,approval_reference=None):
 if not isinstance(dry_run,bool):raise ValueError('dry_run must be boolean')
 if not dry_run:
  if expected_site!=frappe.local.site or not approval_reference:raise ValueError('Explicit site and source approval required')
  frappe.has_permission('Item','create',throw=True)
  frappe.db.sql('select name from `tabItem Group` where name=%s for update',('Finished Goods',))
 values=profile();peers=source_rows();results=[]
 for row in peers:
  items=frappe.get_all('Item',fields=['name','item_name','description'],limit_page_length=0)
  aliases={}
  for dt,field in [('Item Supplier','supplier_part_no'),('Item Customer Detail','ref_code'),('Item Barcode','barcode')]:
   if not frappe.db.exists('DocType',dt) or not frappe.get_meta(dt).has_field(field):continue
   for alias in frappe.get_all(dt,fields=['parent',field],limit_page_length=0):
    if alias.get(field):aliases.setdefault(normcode(alias[field]),[]).append(alias.parent)
  check=classify(row,items,peers,aliases);entry={**row,**check,'created':False};results.append(entry)
  if dry_run or check['category'] not in ('A','C'):continue
  evidence='Source status: '+row['status']+'; FG Item existence is not Manufacturing Ready. Source: FG Standard Updated.xlsx / Sheet1 row '+str(row['row'])+'; SHA256 '+SHA+'; policy '+VERSION+'; approval: '+approval_reference
  doc=frappe.get_doc({'doctype':'Item',**values,'item_code':row['fg'],'item_name':row['item_name'],'description':html.escape(row['item_name'])+'<br>'+html.escape(evidence)})
  doc.insert()
  if doc.name!=row['fg'] or any(doc.get(k)!=v for k,v in PROFILE.items()):raise ValueError('Native Item result differs from approved FG profile')
  if doc.get('default_bom') or doc.get('opening_stock'):raise ValueError('Unexpected BOM/stock authority')
  entry['created']=True
 return {'counts':dict(Counter(r['category'] for r in results)),'created':sum(r['created'] for r in results),'records':results,'profile':values}
