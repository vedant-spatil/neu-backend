import json, sys, time, urllib.request
B = 'http://localhost:8001'
def req(method, path, body=None, token=None):
    h = {'Content-Type': 'application/json'}
    if token: h['Authorization'] = 'Bearer ' + token
    r = urllib.request.Request(B + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=120) as resp: return resp.status, json.loads(resp.read() or b'null')
    except urllib.error.HTTPError as e: return e.code, e.read().decode()[:200]
def login(e, p): return req('POST', '/api/auth/login', {'email': e, 'password': p})[1]['access_token']
adm = login('admin@example.com', 'demo-admin-pass-123')
qa = login('qa@example.com', 'demo-qa-pass-12345')
inv = login('investigator1@example.com', 'demo-investigator-pass-1')
qa_id = req('GET', '/api/auth/me', token=qa)[1]['id']
print('qa projects before member ->', req('GET', '/api/projects', token=qa)[0])
code, p = req('POST', '/api/projects', {'name': 'Project Manual'}, adm); print('create', code, p)
pid = p['id']
print('add member', req('POST', f'/api/projects/{pid}/members', {'user_id': qa_id}, adm)[0])
print('add accounts (manual)', req('POST', f'/api/projects/{pid}/accounts', {'account_ids': ['800056660']}, adm))
print('qa view', req('GET', f'/api/qa/projects/{pid}/accounts', token=qa))
print('qa submit', req('POST', f'/api/qa/projects/{pid}/evaluations?account_id=800056660', {'tags': ['anomaly', 'case_found'], 'notes': 'looks structured'}, qa))
code, evs = req('GET', f'/api/projects/{pid}/evaluations', token=adm); eid = evs[0]['id']
print('admin reject no reason', req('POST', f'/api/evaluations/{eid}/review', {'action': 'reject', 'reason': 'x'}, adm)[0])
print('admin reject', req('POST', f'/api/evaluations/{eid}/review', {'action': 'reject', 'reason': 'typology tag not supported'}, adm)[0])
print('qa resubmit', req('POST', f'/api/qa/evaluations/{eid}/resubmit', {'tags': ['anomaly'], 'notes': 'fixed'}, qa)[1].get('status'))
print('admin accept', req('POST', f'/api/evaluations/{eid}/review', {'action': 'accept'}, adm)[1].get('status'))
print('bad tag', req('POST', f'/api/qa/projects/{pid}/evaluations?account_id=1', {'tags': ['random']}, qa))
print('investigator on projects', req('GET', '/api/projects', token=inv)[0])
print('investigator on admin list', req('GET', f'/api/projects/{pid}/evaluations', token=inv)[0])
# Disabled: auto-validate project (Celery worker and auto drafts are commented out).
# code, p2 = req('POST', '/api/projects', {'name': 'Project Auto', 'auto_validate': True}, adm); pid2 = p2['id']
# req('POST', f'/api/projects/{pid2}/members', {'user_id': qa_id}, adm)
# print('auto add accounts', req('POST', f'/api/projects/{pid2}/accounts', {'account_ids': ['800054E60']}, adm))
# print('qa manual blocked in auto project', req('POST', f'/api/qa/projects/{pid2}/evaluations?account_id=800054E60', {'tags': ['anomaly']}, qa)[0])
# for i in range(40):
#     time.sleep(10)
#     code, evs2 = req('GET', f'/api/projects/{pid2}/evaluations', token=adm)
#     if evs2 and evs2[0]['status'] == 'draft': break
# print('auto draft', evs2[0] if evs2 else None)
# d = evs2[0]['id']
# print('qa validate draft', req('POST', f'/api/qa/evaluations/{d}/validate', {'tags': evs2[0]['tags'], 'notes': 'confirmed'}, qa)[1].get('status'))
# print('admin accept auto', req('POST', f'/api/evaluations/{d}/review', {'action': 'accept'}, adm)[1].get('status'))
