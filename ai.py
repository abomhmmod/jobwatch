# -*- coding: utf-8 -*-
"""الحَكَم: يقرأ المنشور بنموذج Gemini ويحكم: أهو إعلانُ توظيفٍ يُتقدَّم إليه، ومن الجهة، وأين العمل.

judge(text, source, images) يعيد قاموس الحكم، أو None إن تعذّر (لا مفتاح، أو نفدت الحصّة، أو لم يردّ)،
فيرجع البوت حينئذٍ إلى القواعد كما كان، فلا يضيع إعلانٌ بتعطّل الحَكَم. وصور المنشور تُرسل معه فيقرؤها.
read_vacancies(pdf) يقرأ ملفّ شواغر ممسوحًا ضوئيًّا (لا نصّ فيه) من صور صفحاته.
المفتاح في متغيّر البيئة GEMINI_API_KEY، ويُرسل في رأس الطلب لا في الرابط (فلا يظهر في سجلّ).
"""
import base64, json, logging, os, time
from datetime import date

import requests

log = logging.getLogger('jobwatch')
API = 'https://generativelanguage.googleapis.com/v1beta'
# يُختار أوّل المتاح من هذه، إلّا إن عُيّن GEMINI_MODEL. الأسماء «latest» تنتقل وحدها إلى أحدث إصدار.
PREFER = ('gemini-flash-lite-latest', 'gemini-flash-latest', 'gemini-2.5-flash-lite', 'gemini-2.5-flash')
MAX_CHARS = 6000
MAX_IMAGES = 4                 # أكثر صور المنشور التي تُرسل (ألبوم الصور قد يبلغ عشرًا)
IMAGE_MAX = 7 * 1024 * 1024    # الصورة الواحدة
PDF_MAX = 15 * 1024 * 1024     # حدّ الطلب كلّه 20 ميغا
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/130.0 Safari/537.36'}

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
- image_text: إن أُرفقت بالمنشور صور: ما فيها من الإعلان في جملةٍ أو جملتين بالعربيّة (الجهة، والوظائف، والمكان، والموعد)،
  أو null إن لم تُرفق صور أو لم يكن فيها إعلان. الصور جزءٌ من المنشور: احكم في الحقول السابقة بما في النصّ والصور معًا.
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
        'image_text': {'type': 'STRING', 'nullable': True},
        'reason': {'type': 'STRING'},
    },
    'required': ['is_call', 'employer', 'place', 'low_only', 'deadline', 'image_text', 'reason'],
    'propertyOrdering': ['is_call', 'employer', 'place', 'low_only', 'deadline', 'image_text', 'reason'],
}

PDF_PROMPT = """هذا ملفّ شواغر حكوميّ سوريّ (صور صفحاتٍ ممسوحة ضوئيًّا). القارئ رجلٌ من محافظة حماة يحمل إجازةً جامعيّة في اللغة العربيّة.
اقرأ بطاقات الوظائف أو جداولها كلّها، وأجب بحسب ما في الملفّ لا بالتخمين:

- readable: صحيح إن استطعت قراءة الوظائف، وخطأ إن كانت الصفحات غير مقروءة أو لا وظائف فيها.
- has_place_cards: صحيح إن كانت لكلّ وظيفة خانةُ مكان عمل أو محافظة.
- hama_count: عدد الوظائف التي مكان عملها محافظة حماة (أو إحدى مدنها: سلمية، مصياف، السقيلبية، محردة، صوران...) أو جميع المحافظات.
- matching: عناوين الوظائف (من تلك التي تشمل حماة) التي تقبل إجازةً جامعيّة في اللغة العربيّة أو الآداب أو العلوم الإنسانيّة
  أو التربية والتأهيل التربويّ أو الإعلام أو الترجمة، أو تقبل «أيّ اختصاص» أو «جميع الاختصاصات». العنوان كما في الملفّ، بلا تكرار.
- low_only: صحيح إن كانت كلّ الوظائف التي تشمل حماة من الفئتين الرابعة والخامسة أو أعمالًا لا تطلب شهادة.
- reason: جملة عربيّة قصيرة واحدة بما وجدت."""

PDF_SCHEMA = {
    'type': 'OBJECT',
    'properties': {
        'readable': {'type': 'BOOLEAN'},
        'has_place_cards': {'type': 'BOOLEAN'},
        'hama_count': {'type': 'INTEGER'},
        'matching': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
        'low_only': {'type': 'BOOLEAN'},
        'reason': {'type': 'STRING'},
    },
    'required': ['readable', 'has_place_cards', 'hama_count', 'matching', 'low_only', 'reason'],
    'propertyOrdering': ['readable', 'has_place_cards', 'hama_count', 'matching', 'low_only', 'reason'],
}

# القويّة تُجرَّب أوّلًا بالترتيب (طلب المستخدم 2026-11-19: الأقوى ما اتّسعت الحصّة؛ والحَكَم نحو 5 طلبات في اليوم).
# لكلٍّ منها في المستوى المجّانيّ 20 طلبًا في اليوم و5 في الدقيقة، وكثيرًا ما تنشغل: فلكلٍّ محاولةٌ واحدة، وما تعذّر
# يُترك بقيّة التشغيل، ثمّ السريع من PREFER بمحاولاته المعتادة. GEMINI_MODEL إن عُيّن يُستعمل وحده.
STRONG = ('gemini-3-flash-preview', 'gemini-4-hydrogen', 'gemini-3.6-flash', 'gemini-2.5-flash')
STRONG_TIMEOUT = 60

_model = None
_names = set()     # النماذج المتاحة للمفتاح
_rested = set()    # القويّة التي تعذّرت في هذا التشغيل
_down_until = 0.0  # بعد خطأ الحصّة اليوميّة لا يُعاد الطلب في هذا التشغيل


def _key():
    return os.environ.get('GEMINI_API_KEY', '').strip()


def model():
    """اسم النموذج المستعمَل (يُسأل عن المتاح مرّةً في كلّ تشغيل)."""
    global _model, _names
    if _model:
        return _model
    if os.environ.get('GEMINI_MODEL'):
        _model = os.environ['GEMINI_MODEL']
        return _model
    r = requests.get(f'{API}/models', params={'pageSize': 1000}, headers={'x-goog-api-key': _key()}, timeout=30)
    r.raise_for_status()
    names = {m['name'].split('/', 1)[1] for m in r.json().get('models', [])
             if 'generateContent' in m.get('supportedGenerationMethods', [])}
    _names = names
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


def _inline(data, mime):
    return {'inline_data': {'mime_type': mime, 'data': base64.b64encode(data).decode('ascii')}}


def _images(urls):
    """أجزاء الطلب لصور المنشور (أوّل MAX_IMAGES). ما تعذّر تنزيله يُترك، ولا يمنع الحكم بالنصّ."""
    parts = []
    for u in list(urls)[:MAX_IMAGES]:
        try:
            r = requests.get(u, headers=UA, timeout=30)
            mime = (r.headers.get('content-type') or '').split(';')[0].strip()
            if r.ok and mime in ('image/jpeg', 'image/png', 'image/webp') and 0 < len(r.content) <= IMAGE_MAX:
                parts.append(_inline(r.content, mime))
        except requests.RequestException as e:
            log.warning('image not loaded: %s', type(e).__name__)
    return parts


def _ask(parts, schema, check):
    """طلبٌ واحد يجيب بقاموسٍ على المخطّط، أو None إن تعذّر. check: حقلٌ لا بدّ منه في الجواب."""
    global _down_until
    if not _key() or time.time() < _down_until:
        return None
    body = {
        'contents': [{'role': 'user', 'parts': parts}],
        'generationConfig': {'temperature': 0, 'responseMimeType': 'application/json', 'responseSchema': schema},
    }
    try:
        url = f'{API}/models/{model()}:generateContent'
    except Exception as e:
        log.warning('AI unavailable (models): %s', e)
        _down_until = time.time() + 3600
        return None
    if not os.environ.get('GEMINI_MODEL'):
        for m in STRONG:
            if m not in _names or m in _rested:
                continue
            v = _ask_once(m, body, check)
            if v is not None:
                return v
            _rested.add(m)
    for attempt in range(4):
        try:
            r = requests.post(url, json=body, headers={'x-goog-api-key': _key()}, timeout=120)
        except requests.RequestException as e:
            log.warning('AI request failed: %s', type(e).__name__)
            time.sleep(5 * (attempt + 1))
            continue
        if r.status_code == 200:
            try:
                parts = r.json()['candidates'][0]['content']['parts']
                v = json.loads(''.join(p.get('text', '') for p in parts))
                if isinstance(v, dict) and check in v:
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


def _ask_once(m, body, check):
    """محاولةٌ واحدة بنموذجٍ قويّ: الجواب، أو None إن تعذّر بأيّ سبب (فيُترك بقيّة التشغيل)."""
    try:
        r = requests.post(f'{API}/models/{m}:generateContent', json=body, headers={'x-goog-api-key': _key()},
                          timeout=STRONG_TIMEOUT)
    except requests.RequestException as e:
        log.info('AI %s skipped: %s', m, type(e).__name__)
        return None
    if r.status_code != 200:
        log.info('AI %s skipped: HTTP %s', m, r.status_code)
        return None
    try:
        parts = r.json()['candidates'][0]['content']['parts']
        v = json.loads(''.join(p.get('text', '') for p in parts))
        if isinstance(v, dict) and check in v:
            log.info('AI answered by %s', m)
            return v
    except Exception as e:
        log.info('AI %s answer unreadable: %s', m, e)
    return None


def judge(text, source='', images=()):
    """قاموس الحكم، أو None إن تعذّر. images: روابط صور المنشور، تُرسل معه."""
    if not _key() or time.time() < _down_until:
        return None
    prompt = PROMPT.format(today=date.today().isoformat(), source=source,
                           text=text[:MAX_CHARS] or '(لا نصّ؛ المنشور صورٌ وحدها)')
    return _ask([{'text': prompt}] + _images(images), SCHEMA, 'is_call')


def read_vacancies(pdf):
    """ما في ملفّ شواغر ممسوح ضوئيًّا (بايتاته)، أو None إن تعذّر أو لم يُقرأ."""
    if not pdf or len(pdf) > PDF_MAX:
        return None
    v = _ask([_inline(pdf, 'application/pdf'), {'text': PDF_PROMPT}], PDF_SCHEMA, 'readable')
    return v if v and v.get('readable') else None
