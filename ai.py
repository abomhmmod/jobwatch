# -*- coding: utf-8 -*-
"""الحَكَم: يقرأ المنشور بنموذج Gemini ويحكم: أهو إعلانُ توظيفٍ يُتقدَّم إليه، ومن الجهة، وأين العمل.

judge(text, source) يعيد قاموس الحكم، أو None إن تعذّر (لا مفتاح، أو نفدت الحصّة، أو لم يردّ)،
فيرجع البوت حينئذٍ إلى القواعد كما كان، فلا يضيع إعلانٌ بتعطّل الحَكَم.
المفتاح في متغيّر البيئة GEMINI_API_KEY، ويُرسل في رأس الطلب لا في الرابط (فلا يظهر في سجلّ).
"""
import json, logging, os, time
from datetime import date

import requests

log = logging.getLogger('jobwatch')
API = 'https://generativelanguage.googleapis.com/v1beta'
# يُختار أوّل المتاح من هذه، إلّا إن عُيّن GEMINI_MODEL. الأسماء «latest» تنتقل وحدها إلى أحدث إصدار.
PREFER = ('gemini-flash-lite-latest', 'gemini-flash-latest', 'gemini-2.5-flash-lite', 'gemini-2.5-flash')
MAX_CHARS = 6000

PROMPT = """أنت تفرز منشورات حكوميّة سوريّة لرجلٍ من محافظة حماة يبحث عن وظيفة في القطاع العامّ.
اقرأ المنشور وأجب عن الحقول وحدها، بحسب ما في النصّ لا بالتخمين:

- is_call: صحيح فقط إن كان المنشور إعلانًا يدعو الناس الآن إلى التقدّم لوظيفة أو تعاقد أو تعيين أو مسابقة توظيف
  (أو يفتح باب ذلك أو يمدّده). وخطأ إن كان: خبرًا أو تصريحًا يذكر «فرص العمل» أو التوظيف عرَضًا،
  أو نتائج وأسماء ناجحين ومقبولين ومواعيد مقابلات لمسابقة سابقة، أو مناقصة أو مزادًا أو استئجارًا،
  أو قبولًا جامعيًّا أو مفاضلة طلّاب، أو تطوّعًا، أو دورة تدريبيّة أو منحة دراسيّة.
- employer: "gov" إن كانت الجهة التي توظّف حكوميّة (وزارة، مديريّة، محافظة، جامعة حكوميّة، مؤسّسة أو شركة أو هيئة عامّة، مجلس مدينة، مصرف عامّ)،
  و"private" إن كانت شركة خاصّة أو منظّمة أو جمعيّة أو مدرسة خاصّة، و"unclear" إن لم يتبيّن.
- place: أين العمل: "hama" إن ذُكرت محافظة حماة أو إحدى مدنها وبلداتها (سلمية، مصياف، السقيلبية، محردة، صوران، كفرزيتا...) بين أماكن العمل،
  و"all" إن كان الإعلان لكلّ المحافظات أو لعموم سوريا، و"other" إن كان لمحافظات أخرى ليست حماة بينها،
  و"unknown" إن لم يُذكر المكان. (ذِكر دمشق مكانًا لصدور الكتاب أو للخبر ليس مكان عمل.)
- low_only: صحيح إن كانت كلّ الوظائف المعلنة من الفئتين الرابعة والخامسة أو أعمالًا لا تطلب شهادة
  (سائق، حارس، عامل نظافة أو خدمات، مستخدم)، وخطأ إن كان بينها ما يطلب شهادة جامعيّة أو معهدًا أو ثانويّة.
- deadline: آخر موعد للتقديم بصيغة YYYY-MM-DD إن ذُكر صراحةً، وإلّا null. (تاريخ اليوم: {today}؛ وإن ذُكر يوم وشهر بلا سنة فهو أقرب تاريخ قادم.)
- reason: جملة عربيّة قصيرة واحدة تقول لماذا حكمت بما حكمت.

المصدر: {source}
المنشور:
<<<
{text}
>>>"""

SCHEMA = {
    'type': 'OBJECT',
    'properties': {
        'is_call': {'type': 'BOOLEAN'},
        'employer': {'type': 'STRING', 'enum': ['gov', 'private', 'unclear']},
        'place': {'type': 'STRING', 'enum': ['hama', 'all', 'other', 'unknown']},
        'low_only': {'type': 'BOOLEAN'},
        'deadline': {'type': 'STRING', 'nullable': True},
        'reason': {'type': 'STRING'},
    },
    'required': ['is_call', 'employer', 'place', 'low_only', 'deadline', 'reason'],
    'propertyOrdering': ['is_call', 'employer', 'place', 'low_only', 'deadline', 'reason'],
}

_model = None
_down_until = 0.0  # بعد خطأ الحصّة اليوميّة لا يُعاد الطلب في هذا التشغيل


def _key():
    return os.environ.get('GEMINI_API_KEY', '').strip()


def model():
    """اسم النموذج المستعمَل (يُسأل عن المتاح مرّةً في كلّ تشغيل)."""
    global _model
    if _model:
        return _model
    if os.environ.get('GEMINI_MODEL'):
        _model = os.environ['GEMINI_MODEL']
        return _model
    r = requests.get(f'{API}/models', params={'pageSize': 1000}, headers={'x-goog-api-key': _key()}, timeout=30)
    r.raise_for_status()
    names = {m['name'].split('/', 1)[1] for m in r.json().get('models', [])
             if 'generateContent' in m.get('supportedGenerationMethods', [])}
    _model = next((m for m in PREFER if m in names), None)
    if not _model:
        raise RuntimeError('لا نموذج مناسبًا بين المتاح: ' + ', '.join(sorted(names)[:30]))
    log.info('AI model: %s', _model)
    return _model


def _retry_delay(resp):
    try:
        for d in resp.json()['error'].get('details', []):
            if d.get('@type', '').endswith('RetryInfo'):
                return float(d['retryDelay'].rstrip('s'))
    except Exception:
        pass
    return None


def judge(text, source=''):
    """قاموس الحكم، أو None إن تعذّر."""
    global _down_until
    if not _key() or time.time() < _down_until:
        return None
    body = {
        'contents': [{'role': 'user', 'parts': [{'text': PROMPT.format(
            today=date.today().isoformat(), source=source, text=text[:MAX_CHARS])}]}],
        'generationConfig': {'temperature': 0, 'responseMimeType': 'application/json', 'responseSchema': SCHEMA},
    }
    try:
        url = f'{API}/models/{model()}:generateContent'
    except Exception as e:
        log.warning('AI unavailable (models): %s', e)
        _down_until = time.time() + 3600
        return None
    for attempt in range(4):
        try:
            r = requests.post(url, json=body, headers={'x-goog-api-key': _key()}, timeout=90)
        except requests.RequestException as e:
            log.warning('AI request failed: %s', type(e).__name__)
            time.sleep(5 * (attempt + 1))
            continue
        if r.status_code == 200:
            try:
                parts = r.json()['candidates'][0]['content']['parts']
                v = json.loads(''.join(p.get('text', '') for p in parts))
                if isinstance(v, dict) and 'is_call' in v:
                    return v
            except Exception as e:
                log.warning('AI answer unreadable: %s', e)
            return None
        if r.status_code in (429, 500, 502, 503, 504):
            wait = _retry_delay(r)
            msg = r.text[:300]
            if r.status_code == 429 and ('PerDay' in msg or 'per day' in msg.lower()):
                log.warning('AI daily quota reached: %s', msg)
                _down_until = time.time() + 3600
                return None
            time.sleep(min(wait or 10 * (attempt + 1), 65))
            continue
        log.warning('AI HTTP %s: %s', r.status_code, r.text[:300])
        return None
    return None
