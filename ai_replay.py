# -*- coding: utf-8 -*-
"""تجربة الحَكَم على منشورات الأيّام الماضية، دون إرسال ودون تغيير الحالة.

يأخذ كلّ منشور يعرضه البوت على الحَكَم (فيه كلمات توظيف، أو صورةٌ بنصٍّ قصير)، ويحكم عليه:
بالقواعد وحدها، وبالحَكَم على النصّ وحده كما كان، وبالحَكَم على النصّ والصور معًا كما صار
(وفي المنشور بلا صور يتساوى الأخيران فلا يُسأل مرّتين). وملفّات الشواغر الممسوحة ضوئيًّا يقرؤها الحَكَم.
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

jw.expired = lambda dl, dt: False
jw.read_pdf = functools.lru_cache(maxsize=None)(jw.read_pdf)


def verdict(it, src, v=None):
    """هل يُرسل؟ بالقواعد (v=None) أو بحكم الحَكَم v. (رسالة المراجعة الصامتة لا تُعدّ إرسالًا.)"""
    st = {'seen_set': set(), 'codes_set': set()}
    res = jw.evaluate(it, dict(src, assume_hama=it.get('assume_hama', src.get('assume_hama'))), st,
                      judge=lambda *a, **k: v)
    return (bool(res) and not res.get('quiet')), (res or {}).get('hint')


def ask(it, src, images=()):
    v = ai.judge(it['text'], src['name'], images=images)
    time.sleep(gap)
    return v


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
        key, t = it['id'], jw.norm(it['text'])
        in_image = not jw.loose(t) and jw.image_only(it, t)
        if key in seen_urls or not (jw.loose(t) or in_image):
            continue
        # ONLY_CHANGED=1: ما لا صور فيه ولا ملفّ شواغر يُسأل عنه الحَكَم كما كان، فلا حاجة إلى إعادته
        if os.environ.get('ONLY_CHANGED') and not it.get('images') and not any(
                jw.PDF_LINK.search(l) for l in it.get('links', [])):
            continue
        seen_urls.add(key)
        rule, _ = verdict(it, src)
        imgs = it.get('images') or []
        v_new = ask(it, src, imgs)
        v_old = (ask(it, src) if imgs and not in_image else v_new) if not in_image else None
        new, hint = verdict(it, src, v_new) if v_new else (None, None)
        old = (verdict(it, src, v_old)[0] if v_old else None) if not in_image else False
        rows.append({'src': src['name'], 'url': it['url'], 'date': it['date'].isoformat() if it.get('date') else None,
                     'images': len(imgs), 'image_only': in_image, 'text': ' '.join(it['text'].split())[:700],
                     'rules': rule, 'ai_text': old, 'ai_images': new, 'judge_text': v_old, 'judge_images': v_new,
                     'hint': hint})
        mark = '=' if new == old else ('⚠ AI-FAIL' if new is None else ('IMG+' if new else 'IMG-'))
        print(f"{mark:9} rules={rule!s:5} text={old!s:5} images={new!s:5} imgs={len(imgs)}"
              f"{' (صورة وحدها)' if in_image else ''} | {src['name']} | {it['url']}"
              + (f"\n          {v_new.get('reason')}" + (f"\n          🖼 {v_new.get('image_text')}" if v_new.get('image_text') else '')
                 if v_new and new != old else '')
              + (f"\n          {hint}" if hint and 'قرأه الذكاء' in hint else ''), flush=True)

json.dump({'days': days, 'model': ai._model, 'rows': rows}, open('ai_report.json', 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1)
n = len(rows)
print(f"\nالنموذج: {ai._model}. فُحص {n} منشورًا من آخر {days} يومًا "
      f"({sum(r['image_only'] for r in rows)} منها صورةٌ بنصٍّ قصير، و{sum(r['images'] > 0 for r in rows)} فيها صور). "
      f"القواعد تقبل {sum(r['rules'] for r in rows)}، والحَكَم بالنصّ وحده {sum(bool(r['ai_text']) for r in rows)}، "
      f"والحَكَم بالنصّ والصور {sum(bool(r['ai_images']) for r in rows)}، "
      f"وتعذّر الحَكَم في {sum(r['ai_images'] is None for r in rows)}، "
      f"واختلف الحَكَمان في {sum(r['ai_images'] is not None and r['ai_images'] != r['ai_text'] for r in rows)}.")
