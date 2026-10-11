import csv, io, json, os, re, secrets, sqlite3, threading, time
from datetime import timedelta
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse, urlencode
import requests
from flask import Flask, request, session, redirect, render_template, jsonify, Response, abort
from werkzeug.security import check_password_hash
from jinja2 import DictLoader

# All interface assets are embedded: only this file and requirements.txt are needed.
DEFAULT_ACCOUNTS = {'flowforgeweb': {'role': 'admin', 'password_hash': 'scrypt:32768:8:1$legEmsTrRyNjgsOx$aa3b021d63e236f78d2edd7223f8b8fecba7b7b0523531b575ce1708ec0423c1688ba26868a910422f1d2d84c4426a5ec314a31f4744f2ecfbd9ddd0e668e8c9'}}
TEMPLATES = {
'login.html': r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Sign in · FlowForge Web</title><link rel="stylesheet" href="/static/style.css"></head><body class="login"><main class="login-card"><div class="brand">F<span>↗</span> FLOWFORGE WEB</div><p class="eyebrow">AFFILIATE WORKSPACE</p><h1>Your next client<br>starts here.</h1><p class="muted">Find local businesses. Build real connections.</p>{% if not configured %}<p class="notice">Setup required: add USERS_JSON in your server environment. See README.md.</p>{% endif %}{% if error %}<p class="notice">{{error}}</p>{% endif %}<form method="post"><input type="hidden" name="csrf" value="{{session.csrf}}"><label>Username<input name="username" autocomplete="username" required maxlength="80"></label><label>Password<input name="password" type="password" autocomplete="current-password" required></label><button>Sign in →</button></form><p class="muted small">Private access for FlowForge Web and its affiliates.</p></main></body></html>
""",
'index.html': r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="username" content="{{session.user}}"><meta name="csrf" content="{{session.csrf}}"><title>Lead desk · FlowForge Web</title><link rel="stylesheet" href="/static/style.css"><script defer src="/static/app.js"></script></head><body><header><div class="brand">F<span>↗</span> FLOWFORGE WEB <small>LEAD DESK</small></div><div class="account">{{session.user}} · {{'Admin' if is_admin else 'Affiliate'}}<form action="/logout" method="post"><input type="hidden" name="csrf" value="{{session.csrf}}"><button class="quiet">Sign out</button></form></div></header><main><div class="intro"><div><p class="eyebrow">LOCAL BUSINESSES. NEW POSSIBILITIES.</p><h1>Find your next client.</h1><p class="muted">A focused prospecting workspace for Kentucky & its seven neighboring states.</p></div><a class="button secondary" href="/export" id="download">↓ Download call sheet · {{'leads' if is_admin else 'my leads'}}</a></div><div id="message" role="status" aria-live="polite"></div><aside class="notice"><strong>Free Render · temporary session</strong><br>Search only when you press Search. New leads are assigned to you. Download your call sheet before leaving. Server records and assignments can reset after sleep or a restart; keeping a tab open is not a guarantee. This tab keeps a backup for downloads.<div id="reset-actions" hidden><button id="fresh" class="quiet">Download backup & start fresh</button></div></aside><section class="stats"><div><span>In your workspace</span><strong id="total">0</strong></div><div><span>No website found · needs review</span><strong id="potential">0</strong></div><div><span>Available to claim</span><strong id="available">0</strong></div><div><span>Interested / won</span><strong id="interested">0</strong></div></section>
<section class="panel"><div class="section-title"><h2>Discover businesses</h2><span class="pill">MAPS + WEB + FACEBOOK RESULTS</span></div>{% if not ready %}<p class="notice">Live search is not configured. Add your SerpApi key in Render → Environment → SERPAPI_KEY.</p>{% endif %}<form id="scanform" class="scan-form"><label>State<select name="state">{% for code,name in states.items() %}<option value="{{code}}">{{name}}</option>{% endfor %}</select></label><label>City or town<input name="city" placeholder="e.g. Georgetown" required minlength="2" maxlength="80"></label><label>Business type<input name="category" list="categories" placeholder="e.g. lawn care" required minlength="2" maxlength="80"><datalist id="categories"><option>lawn care</option><option>pressure washing</option><option>auto detailing</option><option>barber shop</option><option>cleaning service</option><option>landscaping</option><option>handyman</option><option>restaurant</option><option>roofing</option><option>concrete contractor</option></datalist></label><label>Candidate checks<select name="limit"><option value="5">Up to 5 · ≤11 requests</option><option value="10">Up to 10 · ≤21 requests</option><option value="20">Up to 20 · ≤41 requests</option></select></label><button id="scanbutton" {% if not ready %}disabled{% endif %}>Search ↗</button></form><p class="small muted">One town + business type per scan. Checks the first Maps result page. Searches use your SerpApi quota; no automatic billing upgrades.</p><div id="scans" class="small"></div></section>
<section class="panel"><div class="section-title"><h2>Your call sheet</h2><div class="filters"><input id="filter" aria-label="Search leads" placeholder="Search name, city, phone…"><select id="scope" aria-label="Filter leads"><option value="all">All visible leads</option><option value="potential">No website found</option><option value="mine">Assigned to me</option><option value="available">Available</option></select></div></div><p class="small muted">“No website found” is a search result, not a guarantee. Open the evidence and confirm the business, phone, and website before calling. New search results are assigned to you automatically. Claim any unassigned lead before outreach. Downloads include all statuses for recordkeeping; never call rows marked Do not call.</p><div id="leads"></div></section><footer>FLOWFORGE WEB <span>Discover → Review → Claim → Connect</span></footer></main><template id="lead-template"><article class="lead"><div class="lead-heading"><div><h3></h3><p class="location muted"></p></div><span class="assessment pill"></span></div><div class="lead-body"><div class="contact"><a class="phone"></a><div class="meta"></div><button class="claim secondary">Claim lead</button></div><div class="workflow"><label>Call outcome<select class="status">{% for s in statuses %}<option>{{s}}</option>{% endfor %}</select></label><label>Notes<textarea class="notes" rows="2" maxlength="3000" placeholder="Contact name, follow-up date, next step…"></textarea></label><button class="save quiet">Save changes</button></div></div><details><summary>Review source evidence</summary><div class="evidence"></div></details></article></template></body></html>
""",
}
STATIC_ASSETS = {
'style.css': r""":root{--ink:#142a2b;--muted:#617271;--green:#176e59;--lime:#d9f77b;--paper:#f5f7f3;--line:#dce3dc}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.5 system-ui,-apple-system,sans-serif}header{display:flex;justify-content:space-between;align-items:center;padding:22px 4%;background:#fff;border-bottom:1px solid var(--line)}.brand{font-size:16px;font-weight:850;letter-spacing:1px}.brand span{color:var(--green);margin-right:10px}.brand small{font-size:10px;font-weight:600;color:var(--muted);margin-left:22px;letter-spacing:2px}.account{display:flex;align-items:center;gap:18px;font-size:12px}.account form{margin:0}main{max-width:1350px;margin:auto;padding:36px 4%}.intro{display:flex;justify-content:space-between;align-items:center;gap:20px;margin-bottom:28px}.eyebrow{color:var(--green);font-size:11px;font-weight:800;letter-spacing:2px}h1{font-size:42px;line-height:1.12;letter-spacing:-1.5px;margin:12px 0}h2{font-size:18px;margin:0}h3{font-size:19px;margin:0}.muted{color:var(--muted)}.small{font-size:12px}.stats{display:grid;grid-template-columns:repeat(4,1fr);border:1px solid var(--line);border-radius:14px;overflow:hidden;background:white;margin-bottom:26px}.stats>div{padding:22px;border-right:1px solid var(--line)}.stats>div:first-child{background:var(--ink);color:#fff}.stats span{font-size:12px;display:block}.stats strong{font-size:36px;letter-spacing:-1px;display:block;margin-top:5px}.panel{background:white;border:1px solid var(--line);border-radius:14px;padding:24px;margin-bottom:24px}.section-title{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:18px}.pill{font-size:10px;font-weight:750;letter-spacing:.5px;background:#eef3e8;border-radius:7px;padding:7px 10px}.scan-form{display:grid;grid-template-columns:1fr 1fr 1.15fr 1.15fr auto;gap:12px;align-items:end}label{display:block;font-size:12px;font-weight:650}input,select,textarea{display:block;width:100%;font:inherit;border:1px solid #cdd8d1;border-radius:7px;padding:10px 11px;margin-top:6px;background:#fff;color:var(--ink)}button,.button{display:inline-block;border:0;border-radius:7px;background:var(--green);color:#fff;font:650 13px system-ui;padding:12px 17px;cursor:pointer;text-decoration:none;white-space:nowrap}button:disabled{opacity:.5;cursor:not-allowed}button:hover,.button:hover{filter:brightness(.94)}.secondary{background:var(--lime);color:var(--ink)}.quiet{background:#edf2ed;color:var(--ink)}.filters{display:flex;gap:10px}.filters input,.filters select{margin:0;font-size:12px}.lead{border-top:1px solid var(--line);padding:24px 0}.lead-heading{display:flex;justify-content:space-between;gap:15px;align-items:start}.location{font-size:12px;margin:6px 0}.lead-body{display:grid;grid-template-columns:1fr 1.5fr;gap:24px;margin:12px 0}.phone{font-size:23px;font-weight:700;color:var(--green);text-decoration:none}.meta{font-size:12px;color:var(--muted);margin:8px 0 14px}.workflow{display:grid;grid-template-columns:1fr 1.5fr auto;align-items:end;gap:12px}.workflow textarea{font-size:12px}.evidence a{color:var(--green);overflow-wrap:anywhere}.evidence p{font-size:12px;color:var(--muted);margin-top:3px}.evidence>div{margin:15px 0}summary{cursor:pointer;font-size:12px;color:var(--green)}.notice,#message:not(:empty){padding:14px;background:#fff4d7;border:1px solid #eadcae;border-radius:8px;font-size:13px;margin:12px 0}.empty{text-align:center;padding:55px 20px;color:var(--muted)}.empty b{display:block;font-size:20px;color:var(--ink);margin-bottom:8px}footer{display:flex;justify-content:space-between;font-size:10px;letter-spacing:1px;color:var(--muted);padding:8px 0}.login{min-height:100vh;display:grid;place-items:center;background:var(--ink)}.login-card{background:var(--paper);padding:42px;border-radius:20px;max-width:480px;margin:30px}.login-card .eyebrow{margin-top:34px}.login-card form{margin:26px 0}.login-card label{margin:14px 0}.login-card button{width:100%}:focus-visible{outline:3px solid #8bbcae;outline-offset:3px}@media(max-width:1000px){.scan-form{grid-template-columns:1fr 1fr}.lead-body{grid-template-columns:1fr}.workflow{grid-template-columns:1fr 2fr auto}.section-title{align-items:start;flex-direction:column}}@media(max-width:600px){header{padding:18px}.brand small{display:none}.account{gap:8px}.account button{padding:8px}main{padding:24px 16px}h1{font-size:33px}.intro{align-items:start;flex-direction:column}.stats{grid-template-columns:1fr 1fr}.stats>div{padding:16px}.panel{padding:18px}.scan-form,.workflow{grid-template-columns:1fr}.lead-heading{flex-direction:column}.filters{flex-direction:column;width:100%}.login-card{padding:28px}.stats span{font-size:10px}}
[hidden]{display:none!important}
""",
'app.js': r"""const $=s=>document.querySelector(s);
const user=$('meta[name="username"]').content;
const cacheKey='flowforge-call-sheet-v2:'+user;
let model={leads:[],scans:[],user},dirty=new Set(),busy=false,stale=false,pendingFresh=null;
const configured=!$('#scanbutton').disabled;
function message(t){$('#message').textContent=t;}
function cache(){try{sessionStorage.setItem(cacheKey,JSON.stringify({model,dirty:[...dirty]}));}catch{message('This browser could not keep a tab backup. Download your call sheet now.');}}
function resetNotice(fresh){stale=true;pendingFresh=fresh||pendingFresh;$('#reset-actions').hidden=false;$('#scanbutton').disabled=true;message('Server records have reset. Your previous call sheet and notes are still here in this tab. Download them before starting fresh. Claims cannot be checked against other affiliates until you start fresh.');}
async function post(url,body){let r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':$('meta[name="csrf"]').content},body:JSON.stringify({...body,dataset:model.dataset})});if(!r.ok){let j=await r.json().catch(()=>({}));if(j.reset)resetNotice();throw Error(j.error||`Request failed (${r.status}). Your tab backup is available; sign in again if needed.`);}return r.json();}
function csvCell(v){let s=String(v??'');if(/^[\s]*[=+@-]/.test(s)||/^[\t\r\n]/.test(s))s="'"+s;return '"'+s.replaceAll('"','""')+'"';}
function download(){const keys=['name','phone','address','state','city','category','assessment','owner','status','notes','checked'];const rows=model.leads.filter(l=>model.admin||l.owner===model.user);const csv='\ufeff'+[keys,...rows.map(l=>keys.map(k=>l[k]))].map(row=>row.map(csvCell).join(',')).join('\r\n');const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));let a=document.createElement('a');a.href=url;a.download='flowforge-call-sheet-'+new Date().toISOString().slice(0,10)+'.csv';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
function render(){let all=model.leads;$('#total').textContent=all.length;$('#potential').textContent=all.filter(l=>l.assessment.startsWith('No website')).length;$('#available').textContent=all.filter(l=>!l.owner&&l.status==='New').length;$('#interested').textContent=all.filter(l=>['Interested','Won'].includes(l.status)).length;
let q=$('#filter').value.toLowerCase(),scope=$('#scope').value;let rows=all.filter(l=>[l.name,l.phone,l.city,l.state,l.category].join(' ').toLowerCase().includes(q)).filter(l=>scope==='all'||scope==='potential'&&l.assessment.startsWith('No website')||scope==='mine'&&l.owner===model.user||scope==='available'&&!l.owner&&l.status==='New');$('#leads').replaceChildren();
if(!rows.length){let e=document.createElement('div');e.className='empty';let b=document.createElement('b');b.textContent=all.length?'No matching leads':'Your call sheet starts here';e.append(b,document.createTextNode(all.length?'Try a different filter.':'Choose a town and business type above, then press Search.'));$('#leads').append(e);}
for(let l of rows){let fragment=$('#lead-template').content.cloneNode(true),card=fragment.querySelector('article');card.querySelector('h3').textContent=l.name;card.querySelector('.location').textContent=l.address;card.querySelector('.assessment').textContent=l.assessment;let phone=card.querySelector('.phone');phone.textContent=l.phone;if(l.status!=='Do not call')phone.href='tel:'+l.phone.replace(/[^\d+]/g,'');card.querySelector('.meta').textContent=`${l.category} · ${l.owner?'Assigned to '+l.owner:'Unclaimed'} · Checked ${l.checked} UTC`;let claim=card.querySelector('.claim');claim.hidden=Boolean(l.owner)||l.status!=='New';claim.disabled=stale;claim.onclick=async()=>{try{await post('/api/leads/'+l.id,{action:'claim'});await refresh();}catch(e){message(e.message);}};
let status=card.querySelector('.status'),notes=card.querySelector('.notes'),save=card.querySelector('.save');status.value=l.status;notes.value=l.notes;let canEdit=model.admin||l.owner===model.user;status.disabled=notes.disabled=save.disabled=!canEdit;save.textContent=dirty.has(l.id)?'Save notes':'Saved';
const draft=()=>{l.status=status.value;l.notes=notes.value;dirty.add(l.id);save.textContent='Save notes';if(l.status==='Do not call')phone.removeAttribute('href');else phone.href='tel:'+l.phone.replace(/[^\d+]/g,'');cache();};status.onchange=draft;notes.oninput=draft;
save.onclick=async()=>{cache();if(stale){message('Notes kept in this tab only. Download your call sheet to save them.');return;}save.disabled=true;const snapshot={status:l.status,notes:l.notes};try{await post('/api/leads/'+l.id,snapshot);if(l.status===snapshot.status&&l.notes===snapshot.notes){dirty.delete(l.id);save.textContent='Saved';}cache();message('Notes saved for this server session and backed up in this tab.');}catch(e){message(e.message+' Notes remain in this tab; Download call sheet includes them.');}finally{save.disabled=false;}};
for(let item of l.evidence){let box=document.createElement('div'),a=document.createElement('a'),p=document.createElement('p');a.textContent=item.title;a.href=item.url;a.target='_blank';a.rel='noopener noreferrer';p.textContent=item.snippet;box.append(a,p);card.querySelector('.evidence').append(box);}$('#leads').append(fragment);}}
function renderScans(){if(!$('#scans'))return;$('#scans').replaceChildren();for(let s of model.scans.slice(0,3)){let p=document.createElement('p');p.textContent=`${s.status} · ${s.category} / ${s.city}, ${s.state} · ${s.added} leads saved · ${s.calls} API requests. ${s.message}`;$('#scans').append(p);}let running=model.scans.some(s=>s.status==='Running');$('#scanbutton').disabled=!configured||running||stale;$('#scanbutton').textContent=running&&!stale?'Searching…':'Search ↗';}
async function refresh(poll=false){if(busy||stale)return;busy=true;try{let r=await fetch('/api/data');if(r.status===401){message('Your sign-in expired. Download your call sheet before refreshing to sign in again.');return;}if(!r.ok)throw Error('Could not reach the server. Your call sheet is still available here.');let fresh=await r.json();if(model.dataset&&model.dataset!==fresh.dataset&&model.leads.length){resetNotice(fresh);render();return;}
for(let l of fresh.leads){let old=model.leads.find(o=>o.id===l.id);if(old&&dirty.has(l.id)){l.notes=old.notes;l.status=old.status;}}
let editing=document.activeElement?.closest('.lead');if(poll&&(editing||dirty.size)){model.scans=fresh.scans;const ids=new Set(model.leads.map(l=>l.id));model.leads.push(...fresh.leads.filter(l=>!ids.has(l.id)));cache();renderScans();return;}
model=fresh;cache();render();renderScans();}catch(e){message(e.message);}finally{busy=false;}}
$('#download').onclick=e=>{e.preventDefault();download();};
$('#fresh').onclick=async()=>{download();try{sessionStorage.removeItem(cacheKey);}catch{}dirty.clear();stale=false;model={leads:[],scans:[],user};$('#reset-actions').hidden=true;message('Previous call sheet downloaded. Starting a new server session.');if(pendingFresh){model=pendingFresh;pendingFresh=null;cache();render();renderScans();}else await refresh();};
$('#scanform').addEventListener('submit',async e=>{e.preventDefault();if(stale)return;let d=Object.fromEntries(new FormData(e.target));$('#scanbutton').disabled=true;try{await post('/api/scan',d);message('Searching. New leads are assigned to you as checks finish.');await refresh();}catch(err){message(err.message);$('#scanbutton').disabled=!configured||stale;}});
$('#filter').oninput=render;$('#scope').onchange=render;
try{let cached=JSON.parse(sessionStorage.getItem(cacheKey)||'null');if(cached?.model?.user===user){model=cached.model;dirty=new Set(cached.dirty||[]);render();}}catch{}
refresh();setInterval(()=>{if(!stale&&model.scans.some(s=>s.status==='Running'))refresh(true);},5000);
window.addEventListener('beforeunload',e=>{if(model.leads.length){e.preventDefault();e.returnValue='';}});
""",
}

STATES = {'KY':'Kentucky','OH':'Ohio','IN':'Indiana','IL':'Illinois','MO':'Missouri','TN':'Tennessee','VA':'Virginia','WV':'West Virginia'}
SOCIAL = ('facebook.com','instagram.com','yelp.com','yellowpages.com','bbb.org','mapquest.com','google.com','tripadvisor.com','nextdoor.com','manta.com','chamberofcommerce.com','angi.com','thumbtack.com','linkedin.com','tiktok.com','youtube.com')
STATUSES = ['New','Reviewed — no website','Called','Interested','Won','Not interested','Has website','Do not call']
app = Flask(__name__, static_folder=None)
app.jinja_loader = DictLoader(TEMPLATES)
app.secret_key = os.environ.get('SECRET_KEY') or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=os.environ.get('RENDER')=='true', PERMANENT_SESSION_LIFETIME=timedelta(hours=12), MAX_CONTENT_LENGTH=16384)
DB = os.environ.get('DATABASE_PATH','data/leads.db')
LOCK = threading.Lock()

def db():
    c=sqlite3.connect(DB, timeout=20); c.row_factory=sqlite3.Row
    return c

def init():
    Path(DB).parent.mkdir(parents=True,exist_ok=True)
    with db() as c:
        c.executescript('''CREATE TABLE IF NOT EXISTS leads(id INTEGER PRIMARY KEY, identity TEXT UNIQUE, name TEXT, phone TEXT, address TEXT, state TEXT, city TEXT, category TEXT, assessment TEXT, evidence TEXT, owner TEXT DEFAULT '', status TEXT DEFAULT 'New', notes TEXT DEFAULT '', checked TEXT);
        CREATE TABLE IF NOT EXISTS scans(id INTEGER PRIMARY KEY, state TEXT, city TEXT, category TEXT, status TEXT, calls INTEGER DEFAULT 0, added INTEGER DEFAULT 0, message TEXT DEFAULT '', created TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS attempts(name TEXT PRIMARY KEY, count INTEGER, since REAL);
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT);''')
        if 'owner' not in [r[1] for r in c.execute('PRAGMA table_info(scans)')]:
            c.execute("ALTER TABLE scans ADD COLUMN owner TEXT DEFAULT ''")
        c.execute("INSERT OR IGNORE INTO metadata VALUES('dataset',?)",(secrets.token_hex(16),))
        c.execute("UPDATE scans SET status='Interrupted',message='Server restarted; saved leads retained. Start a new scan.' WHERE status='Running'")
init()

def dataset_id():
    with db() as c: return c.execute("SELECT value FROM metadata WHERE key='dataset'").fetchone()[0]

def accounts():
    try:
        configured=os.environ.get('USERS_JSON')
        return json.loads(configured) if configured else DEFAULT_ACCOUNTS
    except (ValueError,OSError): return {}

def admin(): return accounts().get(session.get('user'),{}).get('role')=='admin'
def guard(fn):
    @wraps(fn)
    def wrapped(*a,**k):
        if session.get('user') not in accounts():
            if request.path.startswith('/api/') or request.path=='/export': abort(401)
            return redirect('/login')
        return fn(*a,**k)
    return wrapped

@app.before_request
def csrf():
    if request.method=='POST' and not secrets.compare_digest(str(session.get('csrf','!')),str(request.headers.get('X-CSRF-Token') or request.form.get('csrf',''))): abort(403)

@app.after_request
def headers(r):
    r.headers['X-Content-Type-Options']='nosniff'; r.headers['X-Frame-Options']='DENY'
    r.headers['Content-Security-Policy']="default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
    r.headers['Cache-Control']='no-store'
    return r

@app.route('/login',methods=['GET','POST'])
def login():
    session.setdefault('csrf',secrets.token_hex(24)); error=''
    if request.method=='POST':
        name=request.form.get('username','').strip().lower()[:80]
        # Persistent per-account throttling (no dependency on trusted proxy IPs).
        with db() as c:
            old=c.execute('SELECT * FROM attempts WHERE name=?',(name,)).fetchone()
            if old and old['count']>=10 and time.time()-old['since']<900:
                return render_template('login.html',error='Too many attempts. Try again in 15 minutes.'),429
            valid=accounts().get(name,{})
            if valid and check_password_hash(valid.get('password_hash',''),request.form.get('password','')):
                c.execute('DELETE FROM attempts WHERE name=?',(name,)); session.clear(); session.update(user=name,csrf=secrets.token_hex(24)); session.permanent=True
                return redirect('/')
            count=old['count']+1 if old and time.time()-old['since']<900 else 1
            since=old['since'] if count>1 else time.time()
            c.execute('INSERT OR REPLACE INTO attempts VALUES(?,?,?)',(name,count,since))
        error='Username or password is incorrect.'
    return render_template('login.html',error=error,configured=bool(accounts()))

@app.post('/logout')
def logout(): session.clear(); return redirect('/login')
@app.get('/static/<name>')
def embedded_asset(name):
    if name not in STATIC_ASSETS: abort(404)
    return Response(STATIC_ASSETS[name],mimetype='text/css' if name.endswith('.css') else 'application/javascript')

@app.get('/health')
def health(): return {'status':'ok'}
@app.get('/')
@guard
def index(): return render_template('index.html',states=STATES,statuses=STATUSES,is_admin=admin(),ready=bool(os.environ.get('SERPAPI_KEY')))

def safe_url(url):
    try: return url if urlparse(str(url)).scheme in ('http','https') and urlparse(url).hostname else ''
    except ValueError: return ''
def directory(url):
    host=(urlparse(safe_url(url)).hostname or '').lower()
    return any(host==d or host.endswith('.'+d) for d in SOCIAL)
def phone_key(phone):
    s=re.sub(r'\D','',str(phone)); s=s[1:] if len(s)==11 and s.startswith('1') else s
    return s if len(s)==10 else ''
def in_state(address,state): return bool(re.search(r'\b'+state+r'\s+\d{5}\b',address,re.I) or re.search(r'\b'+STATES[state]+r'\b',address,re.I))
def api(params,scan_id):
    with db() as c: c.execute('UPDATE scans SET calls=calls+1 WHERE id=?',(scan_id,))
    try:
        r=requests.get('https://serpapi.com/search.json',params={**params,'api_key':os.environ['SERPAPI_KEY'],'hl':'en','gl':'us'},timeout=25)
        if r.status_code!=200: raise RuntimeError(f'Search provider HTTP {r.status_code}; check API key or quota in SerpApi.')
        data=r.json()
        if data.get('error'): raise RuntimeError('Search provider rejected the query. Check SerpApi dashboard for details.')
        return data
    except (requests.RequestException,ValueError): raise RuntimeError('Search provider connection/response failed. Saved leads retained.') from None

def scan(scan_id,state,city,category,limit,owner):
    try:
        places=api({'engine':'google_maps','type':'search','q':f'{category} in {city}, {STATES[state]}, USA'},scan_id).get('local_results',[])
        checked=0
        for p in places:
            name=str(p.get('title','')).strip(); phone=str(p.get('phone','')); address=str(p.get('address',''))
            identity=phone_key(phone)
            if not name or not identity or not in_state(address,state) or p.get('permanently_closed'): continue
            listed=safe_url(p.get('website',''))
            if listed and not directory(listed): continue
            with db() as c:
                if c.execute('SELECT 1 FROM leads WHERE identity=?',(identity,)).fetchone(): continue
            if checked>=limit: break
            checked+=1
            web=api({'engine':'google','q':f'"{name}" "{phone}" {city} {state}'},scan_id)
            fb=api({'engine':'google','q':f'site:facebook.com "{name}" "{city}" {state}'},scan_id)
            evidence=[]; possible=False
            for result in web.get('organic_results',[])[:10]+fb.get('organic_results',[])[:5]:
                link=safe_url(result.get('link',''))
                if not link: continue
                evidence.append({'title':str(result.get('title','')),'url':link,'snippet':str(result.get('snippet',''))})
            # Any independent domain is conservatively flagged for review, not auto-dismissed.
            for result in web.get('organic_results',[])[:10]:
                link=safe_url(result.get('link',''))
                if link and not directory(link): possible=True
            kg=safe_url(web.get('knowledge_graph',{}).get('website',''))
            if kg and not directory(kg):
                possible=True; evidence.append({'title':'Possible website from search','url':kg,'snippet':'Confirm business identity.'})
            if listed: evidence.insert(0,{'title':'Listed social/directory page','url':listed,'snippet':''})
            maps='https://www.google.com/maps/search/?'+urlencode({'api':1,'query':name+' '+address})
            evidence.insert(0,{'title':'Business listing','url':maps,'snippet':address})
            assessment='Possible website — review' if possible else ('No website found — review' if web.get('organic_results') else 'Inconclusive search — review')
            with db() as c:
                cur=c.execute('INSERT OR IGNORE INTO leads(identity,name,phone,address,state,city,category,assessment,evidence,owner,checked) VALUES(?,?,?,?,?,?,?,?,?,?,datetime(\'now\'))',(identity,name,phone,address,state,city,category,assessment,json.dumps(evidence),owner))
                c.execute('UPDATE scans SET added=added+? WHERE id=?',(cur.rowcount,scan_id))
        with db() as c: c.execute("UPDATE scans SET status='Complete',message='First Maps result page checked. Search nearby towns or another category for more coverage.' WHERE id=?",(scan_id,))
    except Exception as e:
        msg=str(e) if isinstance(e,RuntimeError) else 'Scan failed unexpectedly. Saved leads retained; inspect server logs without sharing secrets.'
        with db() as c: c.execute("UPDATE scans SET status='Failed',message=? WHERE id=?",(msg,scan_id))
    finally: LOCK.release()

@app.post('/api/scan')
@guard
def start_scan():
    if not os.environ.get('SERPAPI_KEY'): return {'error':'Add SERPAPI_KEY in Render Environment first.'},400
    data=request.get_json(silent=True) or {}; state=data.get('state'); city=str(data.get('city','')).strip(); category=str(data.get('category','')).strip()
    if data.get('dataset')!=dataset_id(): return {'error':'The server session changed. Download your call sheet, then start a new session.','reset':True},409
    try: limit=int(data.get('limit',5))
    except (ValueError,TypeError): abort(400)
    if state not in STATES or not 2<=len(city)<=80 or not 2<=len(category)<=80 or limit not in (5,10,20): return {'error':'Choose a supported state, town, category, and scan size.'},400
    if not LOCK.acquire(blocking=False): return {'error':'A scan is already running.'},409
    try:
        with db() as c:
            used=c.execute("SELECT COALESCE(SUM(calls),0) FROM scans WHERE date(created)=date('now')").fetchone()[0]
            if used+1+2*limit>int(os.environ.get('DAILY_API_BUDGET','150')):
                LOCK.release(); return {'error':'Daily API request budget would be exceeded. Try a smaller scan or increase DAILY_API_BUDGET.'},429
            user_used=c.execute("SELECT COALESCE(SUM(calls),0) FROM scans WHERE owner=? AND date(created)=date('now')",(session['user'],)).fetchone()[0]
            if not admin() and user_used+1+2*limit>int(os.environ.get('USER_DAILY_API_BUDGET','50')):
                LOCK.release(); return {'error':'Your search allowance for this session/day would be exceeded. Try a smaller scan or contact the admin.'},429
            sid=c.execute("INSERT INTO scans(state,city,category,status,owner) VALUES(?,?,?,'Running',?)",(state,city,category,session['user'])).lastrowid
        threading.Thread(target=scan,args=(sid,state,city,category,limit,session['user']),daemon=True).start()
        return {'id':sid},202
    except Exception: LOCK.release(); raise

@app.get('/api/data')
@guard
def data():
    with db() as c:
        rows=c.execute('SELECT * FROM leads ORDER BY id DESC').fetchall() if admin() else c.execute("SELECT * FROM leads WHERE (owner='' AND status='New') OR owner=? ORDER BY id DESC",(session['user'],)).fetchall()
        leads=[]
        for r in rows:
            item=dict(r)
            if not admin() and not item['owner']: item['notes']=''
            item['evidence']=json.loads(item['evidence']); leads.append(item)
        scans=[dict(r) for r in c.execute('SELECT * FROM scans ORDER BY id DESC LIMIT 10')] if admin() else [dict(r) for r in c.execute('SELECT * FROM scans WHERE owner=? ORDER BY id DESC LIMIT 10',(session['user'],))]
    return {'leads':leads,'scans':scans,'user':session['user'],'admin':admin(),'dataset':dataset_id()}

@app.post('/api/leads/<int:lid>')
@guard
def update(lid):
    body=request.get_json(silent=True) or {}
    if body.get('dataset')!=dataset_id(): return {'error':'Server records have reset. Your call sheet is still in this tab; download it before starting fresh.','reset':True},409
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT * FROM leads WHERE id=?',(lid,)).fetchone()
        if not row: abort(404)
        if body.get('action')=='claim':
            if row['owner'] or row['status']!='New': return {'error':'This lead is already assigned or unavailable.'},409
            c.execute('UPDATE leads SET owner=? WHERE id=?',(session['user'],lid))
        else:
            if not admin() and row['owner']!=session['user']: abort(403)
            status=body.get('status',row['status']); notes=str(body.get('notes',row['notes']))[:3000]
            if status not in STATUSES: abort(400)
            c.execute('UPDATE leads SET status=?,notes=? WHERE id=?',(status,notes,lid))
    return {'ok':True}

def csv_safe(value):
    v=str(value or '')
    return "'"+v if v.lstrip().startswith(('=','+','-','@')) or v.startswith(('\t','\r','\n')) else v
@app.get('/export')
@guard
def export():
    with db() as c:
        rows=c.execute("SELECT * FROM leads ORDER BY id DESC").fetchall() if admin() else c.execute("SELECT * FROM leads WHERE owner=? ORDER BY id DESC",(session['user'],)).fetchall()
    out=io.StringIO(); w=csv.writer(out); keys=['name','phone','address','state','city','category','assessment','owner','status','notes','checked']
    w.writerow(keys)
    for r in rows: w.writerow([csv_safe(r[k]) for k in keys])
    return Response('\ufeff'+out.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename=flowforge-leads.csv'})

if __name__=='__main__': app.run(host='127.0.0.1',port=int(os.environ.get('PORT','8000')))
