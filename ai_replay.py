# -*- coding: utf-8 -*-
"""تجربة الحَكَم على منشورات الأيّام الماضية، دون إرسال ودون تغيير الحالة.

يأخذ كلّ منشور فيه كلمات توظيف (الشبكة الواسعة)، ويحكم عليه مرّتين: بالقواعد وحدها كما يعمل البوت الآن،
وبالحَكَم مكان القواعد في السؤال «أهو إعلان؟ ومن الجهة؟ وأين العمل؟» (وتبقى قراءة ملفّ الشواغر كما هي).
ولا يُنظر في انقضاء آخر موعد، حتّى تُقارن الأحكام نفسها لا تواريخ الإعلانات القديمة.
يكتب ai_report.json ويطبع الخلاف.   python ai_replay.py [الأيّام]
"""
import functools, json, os, sys, time
from datetime import datetime, timedelta, timezone

import jobwatch as jw
import ai

days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
gap = float(os.environ.get('AI_GAP', '5'))  # ثوانٍ بين طلبين، دون حدّ الطلبات في الدقيقة
cfg = json.load(open('config.public.json', encoding='utf-8'))
cutoff = datetime.now(timezone.utc) - timedelta(days=days)

LOOSE = jw.R(r'توظيف|تعيين|تعاقد|شاغر|شواغر|مسابقة|فرص عمل|فرصة عمل|فرصة وظيفية|باب التقدم|طلبات التوظيف')
jw.expired = lambda dl, dt: False
jw.fetch_pdf_text = functools.lru_cache(maxsize=None)(jw.fetch_pdf_text)
RULES = (jw.is_job, jw.is_gov, jw.scope)


def verdict(it, src, v=None):
    """هل يُرسل؟ بالقواعد (v=None) أو بحكم الحَكَم v."""
    if v is None:
        jw.is_job, jw.is_gov, jw.scope = RULES
    else:
        jw.is_job = lambda t: bool(v['is_call'])
        jw.is_gov = lambda t: v['employer'] != 'private'
        jw.scope = lambda t: v['place']
    st = {'seen_set': set(), 'codes_set': set()}
    try:
        return jw.evaluate(it, dict(src, assume_hama=it.get('assume_hama', src.get('assume_hama'))), st) is not None
    finally:
        jw.is_job, jw.is_gov, jw.scope = RULES


rows, seen_urls = [], set()
for src in cfg['sources']:
    if src['type'] not in ('telegram', 'rss') or not src.get('enabled', True):
        continue
    try:
        items = {'telegram': jw.collect_tg, 'rss': jw.collect_rss}[src['type']](src, cutoff)
    except Exception as e:
        print('!!', src['name'], e, flush=True)
        continue
    for it in items:
        key = it['id']
        if key in seen_urls or not LOOSE.search(jw.norm(it['text'])):
            continue
        seen_urls.add(key)
        rule = verdict(it, src)
        v = ai.judge(it['text'], src['name'])
        time.sleep(gap)
        aiv = verdict(it, src, v) if v else None
        rows.append({'src': src['name'], 'url': it['url'], 'date': it['date'].isoformat() if it.get('date') else None,
                     'text': ' '.join(it['text'].split())[:700], 'rules': rule, 'ai': aiv, 'judge': v})
        mark = '=' if aiv == rule else ('⚠ AI-FAIL' if aiv is None else ('AI+' if aiv else 'AI-'))
        print(f"{mark:9} rules={rule!s:5} ai={aiv!s:5} | {src['name']} | {it['url']}"
              + (f"\n          {v.get('reason')}" if v and aiv != rule else ''), flush=True)

json.dump({'days': days, 'model': ai._model, 'rows': rows}, open('ai_report.json', 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1)
n = len(rows)
print(f"\nالنموذج: {ai._model}. فُحص {n} منشورًا فيه كلمات توظيف من آخر {days} يومًا. "
      f"القواعد تقبل {sum(r['rules'] for r in rows)}، والحَكَم يقبل {sum(bool(r['ai']) for r in rows)}، "
      f"وتعذّر الحَكَم في {sum(r['ai'] is None for r in rows)}، واختلفا في {sum(r['ai'] is not None and r['ai'] != r['rules'] for r in rows)}.")
