# -*- coding: utf-8 -*-
"""مقارنة قاعدتين للحكم على الإعلانات على ما في المصادر الآن، دون إرسال ودون تغيير الحالة.
يطبع ما تقبله القاعدة القديمة وترفضه الجديدة، والعكس."""
import json, sys
from datetime import datetime, timedelta, timezone
import jobwatch as jw

days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
cfg = json.load(open('config.public.json', encoding='utf-8'))
cutoff = datetime.now(timezone.utc) - timedelta(days=days)
new_is_job = jw.is_job


def old_is_job(t):
    if jw.EXCLUDE.search(t):
        return False
    if jw.JOB.search(t) or jw.WEAK_JOB.search(t):
        return bool(jw.APPLY.search(t) or jw.ANNOUNCE.search(t))
    return bool(jw.CONTEST.search(t) and jw.CONTEST_CTX.search(t) and jw.ANNOUNCE.search(t) and jw.APPLY.search(t))


def judge(rule, it, src):
    jw.is_job = rule
    st = {'seen_set': set(), 'codes_set': set()}
    return jw.evaluate(it, dict(src, assume_hama=src.get('assume_hama')), st) is not None


n = acc_old = acc_new = 0
for src in cfg['sources']:
    if src['type'] not in ('telegram', 'rss'):
        continue
    try:
        items = {'telegram': jw.collect_tg, 'rss': jw.collect_rss}[src['type']](src, cutoff)
    except Exception as e:
        print('!!', src['name'], e); continue
    for it in items:
        n += 1
        o, w = judge(old_is_job, it, src), judge(new_is_job, it, src)
        acc_old += o; acc_new += w
        if o != w:
            first = ' '.join(it['text'].split())[:160]
            print(f"{'❌ أُسقط' if o else '➕ أُضيف'} | {src['name']} | {it['url']}\n    {first}")
jw.is_job = new_is_job
print(f'\nفُحص {n} منشورًا من آخر {days} يومًا: القديمة قبلت {acc_old}، والجديدة {acc_new}.')
