# -*- coding: utf-8 -*-
"""فحص الوصول: هل يصل هذا الخادم إلى كلّ مصدر في config.public.json؟ لا يرسل شيئًا ولا يغيّر الحالة."""
import json, re, sys, time
import requests

UA = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36'}
cfg = json.load(open('config.public.json', encoding='utf-8'))


def probe(src):
    t = src['type']
    if t == 'telegram':
        url = f"https://t.me/s/{src['channel']}"
        r = requests.get(url, params={'q': src.get('query')} if src.get('query') else None, headers=UA, timeout=30)
        return url, r.status_code, f"{r.text.count('tgme_widget_message_text')} منشورًا"
    if t == 'rss':
        r = requests.get(src['url'], headers=UA, timeout=30)
        return src['url'], r.status_code, f"{len(re.findall(r'<item\\b', r.text))} عنصرًا"
    if t == 'tawzeef':
        url = 'https://api.tawzeefportal.com/api/Seeker/Job/List'
        r = requests.get(url, params={'stateId': src.get('state_id', 14), 'page': 0, 'pageLength': 60},
                         headers=dict(UA, **{'Accept-Language': 'ar', 'TenantId': '1'}), timeout=30)
        try:
            n = len((r.json().get('result') or {}).get('items', []))
        except Exception:
            n = 'ليس JSON'
        return url, r.status_code, f"{n} وظيفة"
    if t == 'webpage':
        r = requests.get(src['url'], headers=UA, timeout=30)
        return src['url'], r.status_code, f"{len(r.content)} بايت"
    return '?', None, 'نوع غير معروف'


ok = bad = 0
for i, s in enumerate(cfg['sources'], 1):
    t0 = time.time()
    try:
        url, code, info = probe(s)
        good = code == 200
    except Exception as e:
        url, code, info, good = s.get('url', s.get('channel', '')), None, type(e).__name__ + ': ' + str(e)[:120], False
    ok += good; bad += not good
    print(f"{i:02d} {'✅ وصل' if good else '❌ لم يصل'} | {code} | {time.time()-t0:4.1f}s | {s['name']} | {url} | {info}", flush=True)

for name, url in (('Telegram API', 'https://api.telegram.org'), ('Google Drive', 'https://drive.google.com'), ('bit.ly', 'https://bit.ly')):
    try:
        print(f"-- {name}: {requests.get(url, headers=UA, timeout=20).status_code}")
    except Exception as e:
        print(f"-- {name}: ❌ {type(e).__name__}")
print(f"\nالخلاصة: وصل {ok}، لم يصل {bad}")
