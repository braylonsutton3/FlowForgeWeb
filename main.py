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
