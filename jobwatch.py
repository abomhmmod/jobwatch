# -*- coding: utf-8 -*-
"""
JobWatch — متتبّع الوظائف الحكومية في حماة، يرسل الجديد إلى بوت تيلغرام.

  python jobwatch.py              تشغيل عاديّ (تشغّله جدولة المهام)
  python jobwatch.py --test       رسالة تجريبية إلى البوت
  python jobwatch.py --fb-login   نافذة فيسبوك لتسجيل الدخول مرّة واحدة
  python jobwatch.py --fb-find "اسم"   البحث عن روابط صفحات فيسبوك
"""
import base64, email.utils, hashlib, html, io, json, logging, os, random, re, sys, time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

import ai

BASE = Path(__file__).resolve().parent
CFG_PATH, STATE_PATH = BASE / 'config.json', BASE / 'state.json'
FB_PROFILE = BASE / 'fb_profile'
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/130.0 Safari/537.36 Edg/130.0'}
_handlers = [logging.FileHandler(BASE / 'jobwatch.log', encoding='utf-8')]
if sys.stderr is not None:  # pythonw على ويندوز بلا stderr؛ وعلى الخادم يظهر السجلّ في سجلّات التشغيل
    _handlers.append(logging.StreamHandler(sys.stderr))
logging.basicConfig(level=logging.INFO, handlers=_handlers, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('jobwatch')
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass


def browser_channel(cfg=None):
    """قناة متصفّح playwright: من JOBWATCH_BROWSER_CHANNEL أو browser_channel في الإعدادات،
    وإلّا msedge على ويندوز وchromium المرفق مع playwright على غيره."""
    ch = os.environ.get('JOBWATCH_BROWSER_CHANNEL') or (cfg or {}).get('browser_channel')
    if ch is None:
        ch = 'msedge' if sys.platform == 'win32' else 'chromium'
    return None if ch in ('', 'chromium', 'bundled') else ch

# ------------------------------------------------------------------ تطبيع النصّ العربيّ
DIGITS = str.maketrans('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹', '01234567890123456789')


def norm(t):
    t = (t or '').translate(DIGITS)
    t = re.sub('[\u064B-\u0652\u0670\u0640\u200b-\u200f\u202a-\u202e\u2066-\u2069]', '', t)
    # PyPDF2 يقلب لام ألف: «األوىل» ← «الأولى»
    t = t.replace('األ', 'الأ').replace('اإل', 'الإ').replace('اآل', 'الآ').replace('اال', 'الا')
    t = re.sub('[إأآٱ]', 'ا', t)
    t = t.replace('ى', 'ي').replace('ة', 'ه').replace('ؤ', 'و').replace('ئ', 'ي')
    return re.sub(r'\s+', ' ', t).strip()


def R(p):
    return re.compile(norm(p))


HAMA = R(r'(حماة|حماه)(?! (الوطن|الديار|الأقصى|الحق|المستقبل|الثورة|الحمى))|سلمية|مصياف|السقيلبية|محردة|صوران|طيبة الإمام|كفرزيتا|اللطامنة')
# «مستخدم/مستخدمين» وظيفةً، لا «اسم المستخدم» ولا «الطريقة المستخدمة» في تعليمات التقديم.
LOW = R(r'عامل نظافة|عمال نظافة|عامل خدمات|سائق|سائقين|حارس|حراس|(?<!اسم )(?<!اسم ال)مستخدم(ين)?(?!\w)'
        r'|عتالة|الفئة الرابعة|الفئة الخامسة')
UNI = R(r'إجازة جامعية|إجازة في|جامعي|الفئة الأولى|ماجستير|دكتوراه|دبلوم دراسات عليا|مدرس|معلم')
# «في مختلف المحافظات السورية» وصفٌ عامّ في ذيل الأخبار، لا يعني أنّ الإعلان يشمل كلّ المحافظات.
ALL_GOV = R(r'(جميع|كافة|كل) ال?محافظات|(?<!مختلف )(?<!عدد من )(?<!بعض )المحافظات (كافة|السورية|جميعها)|جميع أنحاء')
OTHER_GOV = R(r'دمشق|حمص|حلب|إدلب|اللاذقية|طرطوس|القنيطرة|درعا|السويداء|دير الزور|الحسكة|الرقة')
# «فرص عمل» عبارة تتردّد في الأخبار والتقارير، فلا تكفي وحدها: تحتاج إلى طريقة تقديم (APPLY).
WEAK_JOB = R(r'فرص عمل|فرصة عمل|فرصة وظيفية')
JOB = R(r'شواغر|شاغر|طلبات التوظيف|باب التعاقد|باب التوظيف|للتعاقد مع|التعاقد مع'
        r'|حاجتها (إلى|ل)ـ? ?(موظف|عامل|مدرس|معلم|حملة|خريج|كوادر|اختصاص)')
# «التقدّم المحقَّق/نحو…» في الأخبار إنجازٌ لا تقديمُ طلب (خبر سانا 2637336: «فرص عمل» + «التقدم المحقق» مرّ إعلانًا).
APPLY = R(r'(?<!\w)التقدم(?! (المحقق|الحاصل|الملموس|الكبير|الملحوظ|العلمي|التقني|الاقتصادي|نحو))|التقديم|تقديم الطلبات|طلب التوظيف|الراغبين|رابط|مراجعة|استقبال الطلبات|بطاقة الشاغر')
ANNOUNCE = R(r'تعلن|يعلن|أعلنت|أعلن|إعلان|تدعو|يدعو|دعت')
CONTEST, CONTEST_CTX = R(r'مسابقة'), R(r'لشغل|لتعيين|للتعاقد|لاختيار|الفئة')
EXCLUDE = R(r'أسماء الناجحين|أسماء المقبولين|نتائج|مناقصة|عروض أسعار|استدراج عروض|تطوعي|تطوعية|مزاد|استئجار'
            # القبول الجامعيّ: «مفاضلة ملء الشواغر» مقاعدُ طلّاب لا وظائف. («المفاضلة بين المتقدّمين» في إعلان وظيفة لا تُستثنى.)
            r'|مفاضل(ة|ات) (ملء|ال?قبول|ال?دراسات|التعليم|السنة التحضيرية|العامة|الموحدة)|القبول الجامعي'
            r'|شواغر (ال|لل)?(طلاب|قبول)')
GOV = R(r'وزارة|مديرية|محافظة|جامعة|التنمية الإدارية|المؤسسة العامة|الهيئة العامة|مجلس مدينة'
        r'|الجهات العامة|الشركة العامة|المصرف|الأمانة العامة|ديوان')
PRIVATE = R(r'منظمة|جمعية|مؤسسة خيرية|الرابطة|شركة (?!العامة)|مركز تجميل|مطعم|مدارس? خاصة|معهد خاص|صيدلية|عيادة')
STRONG_GOV = R(r'وزارة|التنمية الإدارية|الأمانة العامة|مديرية (ال)?تربية|جامعة حماة|الهيئة العامة|المؤسسة العامة'
               r'|مديرية \S+( \S+)? (في|ب)\s?حماة|محافظة حماة تعلن|تعلن محافظة حماة')
QUAL = [norm(x).replace(' ', '') for x in (
    'لغة عربية', 'اللغة العربية', 'آداب', 'علوم إنسانية', 'أي اختصاص', 'كافة الاختصاصات',
    'جميع الاختصاصات', 'كافة التخصصات', 'جميع التخصصات', 'تأهيل تربوي', 'تربية', 'معلم صف',
    'إعلام', 'ترجمة', 'لسانيات', 'تدقيق لغوي')]
TITLE_CUT = re.compile(r'\s(?:دمشق|ريف|حمص|حما[ةه]|حلب|[إا]دلب|اللاذقي[ةه]|طرطوس|القنيطر[ةه]|درعا'
                       r'|السويداء|دير الزور|الحسك[ةه]|الرق[ةه]|جميع|كاف[ةه]|الفئ[ةه])')
SCOPE_AR = {'hama': 'حماة', 'all': 'جميع المحافظات (ومنها حماة)', 'unknown': 'لم تُذكر المحافظة في النصّ',
            'pdf': 'حماة في ملفّ الشواغر (والنصّ يذكر محافظات أخرى)'}
# «دمشق في 9/11/2026» في رأس الكتب الرسميّة مكانُ صدورها، لا مكانُ العمل.
DATELINE = R(r'دمشق ?(في)? ?:? ?\d{1,4} ?[/\-.]')


def scope(t):
    t = DATELINE.sub(' ', t)
    if HAMA.search(t):
        return 'hama'
    if ALL_GOV.search(t):
        return 'all'
    return 'other' if OTHER_GOV.search(t) else 'unknown'


def is_job(t):
    if EXCLUDE.search(t):
        return False
    if JOB.search(t):
        return bool(APPLY.search(t) or ANNOUNCE.search(t))
    if WEAK_JOB.search(t):
        return bool(APPLY.search(t))
    return bool(CONTEST.search(t) and CONTEST_CTX.search(t) and ANNOUNCE.search(t) and APPLY.search(t))


def is_gov(t):
    return bool(STRONG_GOV.search(t)) or (bool(GOV.search(t)) and not PRIVATE.search(t))


def kind(t):
    k = []
    if re.search(norm('تعاقد|التعاقد|عقود? سنوي|عقد سنوي'), t):
        k.append('تعاقد')
    if re.search(norm('تعيين|تثبيت|على الملاك'), t):
        k.append('تعيين')
    return ' / '.join(k) or 'غير مذكور'


# ------------------------------------------------------------------ آخر موعد للتقديم
MONTHS = {norm(k): v for k, v in {
    'كانون الثاني': 1, 'يناير': 1, 'شباط': 2, 'فبراير': 2, 'آذار': 3, 'مارس': 3, 'نيسان': 4,
    'أبريل': 4, 'إبريل': 4, 'أيار': 5, 'مايو': 5, 'حزيران': 6, 'يونيو': 6, 'تموز': 7, 'يوليو': 7,
    'آب': 8, 'أغسطس': 8, 'أيلول': 9, 'سبتمبر': 9, 'تشرين الأول': 10, 'أكتوبر': 10,
    'تشرين الثاني': 11, 'نوفمبر': 11, 'كانون الأول': 12, 'ديسمبر': 12}.items()}
MONTH_RE = '|'.join(sorted(map(re.escape, MONTHS), key=len, reverse=True))
DL_ANCHOR = R(r'آخر موعد|لغاية|حتى تاريخ|ينتهي التقديم|موعد أقصاه|حتى نهاية دوام|تمديد')


def mkdate(y, m, d):
    try:
        return date(y, m, d)
    except ValueError:
        return None


def anchored_date(s):
    """أوّل تاريخ في المقطع الذي يلي عبارة الموعد."""
    d = re.search(r'(\d{1,2})\s*[/\-.]\s*(\d{1,2})\s*[/\-.]\s*(\d{4})', s)
    if d:
        return mkdate(int(d[3]), int(d[2]), int(d[1]))
    d = re.search(r'(\d{4})\s*[/\-.]\s*(\d{1,2})\s*[/\-.]\s*(\d{1,2})', s)
    if d:
        return mkdate(int(d[1]), int(d[2]), int(d[3]))
    d = re.search(rf'(\d{{1,2}})\s+({MONTH_RE})\s+(\d{{4}})', s)
    if d:
        return mkdate(int(d[3]), MONTHS[d[2]], int(d[1]))
    return None


def deadline(t):
    """أحدث التواريخ التي تلي عبارات الموعد: إعلان التمديد يذكر الموعد القديم ثمّ الجديد."""
    dates = [anchored_date(t[m.end(): m.end() + 90]) for m in DL_ANCHOR.finditer(t)]
    return max((d for d in dates if d), default=None)


def expired(dl, dt):
    """انتهى باب التقديم: مضى موعده، أو لا موعد معروفًا له ونُشر قبل أكثر من 14 يومًا."""
    if dl:
        return dl < date.today()
    return bool(dt and dt < datetime.now(timezone.utc) - timedelta(days=14))


# ------------------------------------------------------------------ المصادر: تيلغرام
def tg_page(channel, q=None, before=None):
    params = {k: v for k, v in (('q', q), ('before', before)) if v}
    r = requests.get(f'https://t.me/s/{channel}', params=params, headers=UA, timeout=40)
    r.raise_for_status()
    out = []
    for m in BeautifulSoup(r.text, 'html.parser').select('div.tgme_widget_message'):
        post, tx = m.get('data-post') or '', m.select_one('.tgme_widget_message_text')
        # صور المنشور: يقرؤها الحَكَم مع نصّه، فتفاصيل كثيرٍ من الإعلانات في صورتها وحدها
        images = [u[1] for u in (re.search(r"url\('([^']+)'\)", p.get('style', ''))
                                 for p in m.select('.tgme_widget_message_photo_wrap')) if u]
        if not post or not (tx or images):
            continue
        tm = m.select_one('a.tgme_widget_message_date time[datetime]') or m.select_one('time[datetime]')
        it = {'id': 'tg:' + post, 'text': tx.get_text('\n') if tx else '', 'url': 'https://t.me/' + post,
              'date': datetime.fromisoformat(tm['datetime']) if tm else None,
              'links': [a.get('href', '') for a in tx.select('a[href]')] if tx else [], 'images': images}
        if not tx:
            it['key'] = post  # بلا نصّ تتطابق البصمات كلّها، فيكون المنشور نفسه بصمته
        out.append(it)
    return out


# كلّ دالّة جمع تأخذ: المصدر، وأقدم تاريخ يُجمع، وseen (ما عولج من قبل، فتستطيع أن تتخطّى العمل عليه).
def collect_tg(src, cutoff, seen=frozenset()):
    items, before = [], None
    for _ in range(src.get('max_pages', 6)):
        page = tg_page(src['channel'], src.get('query'), before)
        if not page:
            break
        items += page
        dates = [i['date'] for i in page if i['date']]
        if dates and min(dates) < cutoff:
            break
        before = min(int(i['id'].rsplit('/', 1)[1]) for i in page)
        time.sleep(1.5)
    return [i for i in items if not i['date'] or i['date'] >= cutoff]


# ------------------------------------------------------------------ المصادر: RSS (سانا، الفداء)
CONTENT_NS = '{http://purl.org/rss/1.0/modules/content/}encoded'


def _rss_tag(block, name):
    m = re.search(rf'<{name}(?:\s[^>]*)?>(.*?)</{name}>', block, re.S)
    v = (m.group(1) if m else '').strip()
    return re.sub(r'^<!\[CDATA\[|\]\]>$', '', v).strip()


def collect_rss(src, cutoff, seen=frozenset()):
    r = requests.get(src['url'], headers=UA, timeout=40)
    r.raise_for_status()
    out = []
    for blk in re.findall(r'<item\b.*?</item>', r.content.decode('utf-8', 'replace'), re.S):
        title, link = html.unescape(_rss_tag(blk, 'title')), html.unescape(_rss_tag(blk, 'link'))
        pd = _rss_tag(blk, 'pubDate')
        dt = email.utils.parsedate_to_datetime(pd) if pd else None
        if dt and dt < cutoff:
            continue
        soup = BeautifulSoup(_rss_tag(blk, 'content:encoded') or html.unescape(_rss_tag(blk, 'description')), 'html.parser')
        text = soup.get_text('\n')
        links = [a.get('href', '') for a in soup.select('a[href]')] + re.findall(r'https?://\S+', text)
        # صفحة الإعلانات تُقسم إعلانًا إعلانًا، إلّا صفحةً عولجت كاملةً من قبل (فلا تتكرّر إعلاناتها).
        if src.get('split_ads') and 'اعلانات' in norm(title) and 'rss:' + link not in seen:
            blocks, cur = [], []
            for p in soup.find_all('p'):
                pt = p.get_text(' ', strip=True)
                if norm(pt) == 'اعلان':
                    if cur:
                        blocks.append('\n'.join(cur))
                    cur = []
                elif pt:
                    cur.append(pt)
            if cur:
                blocks.append('\n'.join(cur))
            for b in blocks:
                out.append({'id': f'rss:{link}#{sha(b)}', 'text': b, 'date': dt, 'url': link,
                            'links': re.findall(r'https?://\S+', b)})
        else:
            out.append({'id': 'rss:' + link, 'text': title + '\n' + text, 'date': dt, 'url': link,
                        'links': links})
    return out


def sha(t):
    """معرّف قطعة من صفحة إعلانات (من أوّل النصّ؛ لا يُغيَّر حتّى لا تتغيّر معرّفات ما حُفظ)."""
    return hashlib.sha1(norm(t)[:400].encode('utf-8')).hexdigest()[:12]


def fingerprint(t):
    """بصمة النصّ كلّه بلا روابط ولا وسوم ولا رموز: نسخ الإعلان نفسه في المصادر تتطابق،
    والإعلانان اللذان يبدآن بالديباجة نفسها ويختلفان بعدها لا يتطابقان."""
    body = re.sub(r'https?://\S+|#\S+', ' ', norm(t))
    return hashlib.sha1(re.sub(r'[\W_]+', '', body).encode('utf-8')).hexdigest()[:12]


# ------------------------------------------------------------------ المصادر: بوابة التوظيف (tawzeefportal)
TZ_API = 'https://api.tawzeefportal.com/api/Seeker/Job/'
TZ_H = dict(UA, **{'Accept-Language': 'ar', 'TenantId': '1'})
GOV_CO = R(r'وزارة|الهيئات والمؤسسات العامة|حكوم|محافظة|مديرية|جامعة|بلدية|مجلس مدينة|الجهات العامة')
GOV_IND = {'الخدمات الحكومية', 'الإدارة المحلية والمجالس البلدية'}


def collect_tawzeef(src, cutoff, seen=frozenset()):
    j = requests.get(TZ_API + 'List', params={'stateId': src.get('state_id', 14), 'page': 0,
                                              'pageLength': 60}, headers=TZ_H, timeout=40).json()
    out = []
    for it in (j.get('result') or {}).get('items', []):
        co = (it.get('company') or {}).get('companyName') or ''
        ind = (it.get('industry') or {}).get('name') or ''
        if not (GOV_CO.search(norm(co)) or ind in GOV_IND):
            continue
        exp = (it.get('expiresAt') or '')[:10]
        if exp and date.fromisoformat(exp) < date.today():
            continue
        if f"tz:{it['id']}" in seen:
            # عولج من قبل: يكفي معرّفه (ليُعرف أنّه ما زال منشورًا) بلا طلب تفاصيله
            out.append({'id': f"tz:{it['id']}", 'text': '', 'url': '', 'date': None, 'links': []})
            continue
        try:
            d = requests.get(TZ_API + 'Details', params={'id': it['id']}, headers=TZ_H, timeout=40).json()['result']
        except Exception:
            d = it
        desc = BeautifulSoup(d.get('description') or '', 'html.parser').get_text('\n').strip()
        req = BeautifulSoup(d.get('requirements') or '', 'html.parser').get_text('\n').strip()
        text = f"{it['title']} — {co}\n{desc}\n{req}".strip()
        ns = norm(text).replace(' ', '')
        hits = ('اجازه' in ns or 'جامعي' in ns) and any(q in ns for q in QUAL)
        pub = it.get('publishedAt')
        out.append({'id': f"tz:{it['id']}", 'text': text, 'url': f"https://www.tawzeefportal.com/jobs/{it['slug']}",
                    'date': datetime.fromisoformat(pub.replace('Z', '+00:00')) if pub else None, 'links': [],
                    'direct': {'scope': 'hama', 'deadline': date.fromisoformat(exp) if exp else deadline(norm(text)),
                               'hint': '🎯 قد يناسب شهادتك' if hits else None, 'kind': kind(norm(text))}})
        time.sleep(0.8)
    return out


# ------------------------------------------------------------------ المصادر: صفحة تُراقَب حتى تظهر فيها وظائف
def collect_webpage(src, cutoff, seen=frozenset()):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(channel=browser_channel(src), headless=True)
        try:
            pg = b.new_page()
            pg.goto(src['url'], timeout=90000, wait_until='networkidle')
            pg.wait_for_timeout(4000)
            text = pg.inner_text('main') if pg.locator('main').count() else pg.inner_text('body')
        finally:
            b.close()
    if norm(src['empty_marker']) in norm(text):
        return []
    body = text.split(src.get('start_after', ''), 1)[-1] if src.get('start_after') else text
    # بصمة الصفحة كلّها لا أوّلها: وظيفة تُضاف في آخر القائمة تغيّرها أيضًا
    return [{'id': 'page:' + fingerprint(body), 'text': f"🆕 تغيّرت صفحة «{src['name']}» وظهر فيها محتوى جديد:\n{body}",
             'url': src['url'], 'date': datetime.now(timezone.utc), 'links': [],
             'direct': {'scope': 'hama', 'deadline': None, 'hint': None, 'kind': 'افتح الصفحة'}}]


# ------------------------------------------------------------------ ملفّ الشواغر (PDF)
def fetch_pdf(url):
    """(نصّ الملفّ أو None، بايتاته أو None). الملفّ الممسوح ضوئيًّا يعيد نصًّا None وبايتاته."""
    r = requests.get(url, headers=UA, timeout=60, allow_redirects=True)
    data = r.content
    if not data.startswith(b'%PDF'):
        m = re.search(r'/d/([\w-]{20,})', r.url) or re.search(r'[?&]id=([\w-]{20,})', r.url)
        if not m:
            return None, None
        data = b''
        for u, p in (('https://drive.google.com/uc', {'export': 'download', 'id': m[1]}),
                     ('https://drive.usercontent.google.com/download',
                      {'id': m[1], 'export': 'download', 'confirm': 't'})):
            data = requests.get(u, params=p, headers=UA, timeout=90).content
            if data.startswith(b'%PDF'):
                break
        if not data.startswith(b'%PDF'):
            return None, None
    try:
        from pypdf import PdfReader
    except ImportError:
        from PyPDF2 import PdfReader
    try:
        text = '\n'.join((pg.extract_text() or '') for pg in PdfReader(io.BytesIO(data)).pages)
    except Exception as e:
        log.warning('PDF text: %s', e)
        text = ''
    # الملفّ الممسوح ضوئيًّا (صور) يعطي صفحات فارغة: لا نصّ فيه، فيقرؤه الحَكَم من صوره (read_pdf)
    return (text if len(re.findall(r'[^\W\d_]', text)) >= 40 else None), data


def fetch_pdf_text(url):
    return fetch_pdf(url)[0]


def read_pdf(url):
    """ما في ملفّ الشواغر: {'text', 'count', 'match', 'cards', 'by_ai', 'low_only'}، أو None إن لم يُقرأ.
    count: الوظائف التي تشمل حماة، match: عناوين ما يناسب الشهادة، cards: فيه بطاقات «محافظة العمل».
    الملفّ النصّيّ يُحلَّل بالقواعد، والممسوح ضوئيًّا يقرؤه الحَكَم من صور صفحاته (by_ai)."""
    text, data = fetch_pdf(url)
    if text:
        count, match = analyse_pdf(text)
        return {'text': text, 'count': count, 'match': match, 'cards': 'محافظه العمل' in norm(text),
                'by_ai': False, 'low_only': None}
    v = ai.read_vacancies(data) if data else None
    if not v:
        return None
    return {'text': None, 'count': int(v.get('hama_count') or 0), 'match': [str(t) for t in v.get('matching') or []],
            'cards': bool(v.get('has_place_cards')), 'by_ai': True, 'low_only': bool(v.get('low_only'))}


def analyse_pdf(text):
    """يعيد: (عدد الوظائف التي تشمل حماة، عناوين ما يناسب الشهادة)."""
    cards, cur = [], None
    for line in text.splitlines():
        n = norm(line)
        if 'محافظه العمل' in n:
            if cur:
                cards.append(cur)
            cur = []
        elif 'المهام' in n and 'الرييسيه' in n:
            # ما قبل «المهام الرئيسية» في السطر نفسه آخرُ بيانات البطاقة (وقد يكون فيه الاختصاص)
            head = line[:line.find('المهام')] if 'المهام' in line else ''
            if cur is not None and head.strip():
                cur.append(head)
            if cur:
                cards.append(cur)
            cur = None
        elif cur is not None and line.strip():
            cur.append(line)
    if cur:
        cards.append(cur)
    if not cards:
        cards = [text.splitlines()]
    count, match = 0, []
    for c in cards:
        cn = norm(' '.join(c))
        if not (HAMA.search(cn) or ALL_GOV.search(cn)):
            continue
        count += 1
        ns = cn.replace(' ', '')
        if ('اجازه' in ns or 'جامعي' in ns) and any(q in ns for q in QUAL):
            title = TITLE_CUT.split(' ' + c[0].strip(), 1)[0].strip() or c[0].strip()
            ok = sum(1 for ch in title if '\u0600' <= ch <= '\u06ff' or ch in ' ()-/،') >= 0.85 * max(len(title), 1)
            match.append(title[:70] if ok and title else '؟')
    return count, match


# ------------------------------------------------------------------ الحكم على الإعلان
PDF_LINK = re.compile(r'bit\.ly/hiring|drive\.google\.com/file')
MAX_NAMED = 10  # أكثر عناوين الوظائف المناسبة التي تُكتب في الرسالة


# الشبكة الواسعة: ما فيه كلمة توظيف يُعرض على الحَكَم (ai.py)، وما خلا منها لا يُسأل عنه.
# تشمل كلّ ما تلتقطه القواعد (JOB وWEAK_JOB وCONTEST)، فلا يفوت الحَكَمَ ما كانت القواعد ستراه.
LOOSE = R(r'توظيف|تعيين|تعاقد|شاغر|شواغر|مسابقة|فرص عمل|فرصة عمل|فرصة وظيفية|باب التقدم|طلبات التوظيف')


def loose(t):
    return bool(LOOSE.search(t) or JOB.search(t) or WEAK_JOB.search(t) or CONTEST.search(t))


SHORT_TEXT = 200  # منشورٌ بصورة ونصٍّ أقصر من هذا قد يكون إعلانُه كلّه في الصورة، فيُعرض على الحَكَم ولو خلا من كلمات التوظيف


def image_only(it, t):
    return bool(it.get('images')) and len(t.strip()) < SHORT_TEXT


class JudgeUnavailable(Exception):
    """منشورٌ لا تحكم فيه القواعد (إعلانه في صورته) وتعذّر الحَكَم: يُعاد في الفحص التالي ولا يُسجَّل."""


def evaluate(it, src, state, pending=frozenset(), judge=None):
    """يحكم على المنشور دون أن يغيّر الحالة. ما يُسجَّل بعد وصول الرسالة يعود في النتيجة
    (marks: البصمة، codes: رموز ملفّات الشواغر). pending: ما قُبل في هذا الفحص ولم يُرسل بعد.

    الحَكَم (Gemini) يجيب عن ثلاثة أسئلة: أهو إعلانٌ يُتقدَّم إليه؟ أحكوميّةٌ الجهة؟ أين العمل؟
    وتُحسب القواعد بجانبه: إن تعذّر الحَكَم حكمت القواعد (ai=None في النتيجة)، وإن رفض الحَكَم ما كانت
    القواعد سترسله عاد المنشور بعلامة quiet فيُرسل رسالةً صامتة للمراجعة بدل أن يختفي.
    judge: للتجربة (ai_replay.py)؛ الأصل ai.judge.
    صور المنشور تُرسل إلى الحَكَم مع نصّه، والمنشور بصورةٍ ونصٍّ قصير يُعرض عليه وإن خلا من كلمات التوظيف."""
    t = norm(it['text'])
    in_image = not loose(t) and image_only(it, t)
    if not loose(t) and not in_image:
        return None
    links = it.get('links', [])
    pdf = next((l for l in links if PDF_LINK.search(l)), None)
    fp = 'fp:' + (it.get('key') or fingerprint(it['text']))
    codes = set(re.findall(r'(?:hiring|applying)\d{6,}', it['text'] + ' ' + ' '.join(links)))
    hiring = {c for c in codes if c.startswith('hiring')}
    if fp in state['seen_set'] or fp in pending or hiring & (state['codes_set'] | pending):
        return None
    trusted = bool(src.get('trusted_gov'))
    pdf_cache = {}
    by_rules = decide(it, src, t, pdf, is_job(t), trusted or is_gov(t), scope(t), None, pdf_cache)
    v = (judge or ai.judge)(it['text'], it.get('source_name') or src['name'], images=it.get('images') or ())
    if v is None and in_image:
        if judge is None and ai._key():
            raise JudgeUnavailable(it['id'])
        return None
    if v is None:
        res = by_rules
    else:
        place = v.get('place') if v.get('place') in ('hama', 'all', 'other', 'unknown') else 'unknown'
        res = decide(it, src, t, pdf, bool(v.get('is_call')), trusted or v.get('employer') != 'private',
                     place, v, pdf_cache)
        if res:
            res['ai_only'] = by_rules is None
        elif by_rules:
            res = dict(by_rules, quiet=True)
    if res:
        res.update(ai=v, marks={fp}, codes=hiring)
    return res


def decide(it, src, t, pdf, job, gov, sc, v, pdf_cache):
    """بقيّة الحكم بعد الأسئلة الثلاثة: المكان، وآخر موعد، وملفّ الشواغر، والفئات الدنيا.
    v: جواب الحَكَم أو None (القواعد وحدها)."""
    if not job or not gov:
        return None
    if it.get('strict_gov') and not STRONG_GOV.search(t):
        return None
    if sc == 'other' and not pdf:
        return None  # محافظة أخرى، ولا ملفّ شواغر قد تكون فيه حماة
    if sc == 'unknown' and src.get('assume_hama'):
        sc = 'hama'
    dl = deadline(t)
    if dl is None and v and v.get('deadline'):
        try:
            dl = date.fromisoformat(v['deadline'][:10])
        except ValueError:
            pass
    if expired(dl, it.get('date')):
        return None
    hint, ktext, facts = None, t, None
    if pdf:
        if pdf not in pdf_cache:
            try:
                pdf_cache[pdf] = read_pdf(pdf)
            except Exception as e:
                log.warning('PDF %s: %s', pdf, e)
                pdf_cache[pdf] = None
        facts = pdf_cache[pdf]
        if facts:
            if facts['text']:
                ktext += ' ' + norm(facts['text'])
            count, match = facts['count'], facts['match']
            # ملفّ فيه بطاقات «محافظة العمل» وليس في أيّ منها حماة: لا وظيفة لحماة، ولو قال النصّ «جميع المحافظات»
            if count == 0 and (sc in ('unknown', 'other') or (sc == 'all' and facts['cards'])):
                return None
            if sc == 'other':
                sc = 'pdf'  # النصّ يذكر محافظات أخرى، وفي ملفّ الشواغر وظائف تشمل حماة
            where = 'ملفّ الشواغر' + (' (قرأه الذكاء من صور صفحاته)' if facts['by_ai'] else '')
            if count == 0:
                hint = f'📄 لم أجد حماة صراحةً في {where}، راجعه بنفسك.'
            elif match:
                named = [m for m in dict.fromkeys(match) if m != '؟']
                shown = '، '.join(named[:MAX_NAMED]) + (
                    f'، وغيرها ({len(named) - MAX_NAMED})' if len(named) > MAX_NAMED else '')
                hint = (f'📄 عدد الوظائف التي تشمل حماة في {where}: {count}، منها {len(match)} قد تناسب شهادتك'
                        + (': ' + shown if named else ' (عناوينها غير مقروءة آليًّا، افتح الملفّ)'))
            else:
                hint = (f'📄 عدد الوظائف التي تشمل حماة في {where}: {count}، ولم أجد بينها اختصاص '
                        'اللغة العربية أو «أيّ اختصاص» صراحةً. راجعه بنفسك.')
        elif sc == 'other':
            return None  # محافظة أخرى، وملفّ الشواغر غير مقروء
        else:
            hint = '📄 تعذّرت قراءة ملفّ الشواغر آليًّا، راجعه بنفسك.'
    # وظائف الفئتين الرابعة والخامسة فقط لا تخصّ شهادته: في ملفّ الشواغر النصّيّ تحكم القاعدة، وفي الممسوح
    # ضوئيًّا ما قرأه الحَكَم من صوره، وفي نصّ المنشور الحَكَمُ (وإن تعذّر فالقاعدة).
    if facts and facts['by_ai']:
        if facts['low_only']:
            return None
    elif v and not facts:
        if v.get('low_only'):
            return None
    elif LOW.search(ktext) and not UNI.search(ktext):
        return None
    if dl is None and pdf is None and sc == 'unknown' and not src.get('trusted_gov'):
        return None
    return {'item': it, 'src': it.get('source_name') or src['name'], 'scope': sc,
            'deadline': dl, 'hint': hint, 'kind': kind(ktext)}


# ------------------------------------------------------------------ تيلغرام: الإرسال
NOISE = R(r'^(#\S+\s*)+$|^(أعجبني|تعليق|مشاركة|إرسال|عرض المزيد|See more|Like|Comment|Share)$')


def excerpt(text, n=900):
    lines = [l.strip() for l in text.splitlines()]
    lines = [l for l in lines if l and not NOISE.match(norm(l))]
    s = '\n'.join(lines)
    return s if len(s) <= n else s[:n].rsplit(' ', 1)[0] + ' …'


TG_MAX = 4000  # حدّ تيلغرام 4096 حرفًا، ويُترك هامش


def fmt(res, limit=TG_MAX):
    """نصّ الرسالة. إن طال قُصِّر المقتطف ثمّ التلميح، ويبقى الرابط في آخرها."""
    it, e = res['item'], html.escape
    head = f"🏛 <b>{e(res['src'])}</b>"
    if it.get('date'):
        head += ' — ' + it['date'].astimezone().strftime('%Y-%m-%d')
    info = [f"📍 النطاق: {SCOPE_AR[res['scope']]}", f"📑 نوع التوظيف: {res['kind']}"]
    if res['deadline']:
        info.append(f"⏳ آخر موعد: {res['deadline']:%d/%m/%Y}")
    v = res.get('ai')
    if v and v.get('image_text') and it.get('images'):
        info.insert(0, f"🖼 من الصورة: {e(v['image_text'])}")
    if v:
        info.append(f"🤖 {e(v.get('reason', ''))}"
                    + (' (قبله الحَكَم وحده، والقواعد كانت سترفضه)' if res.get('ai_only') else ''))
    elif 'ai' in res and os.environ.get('GEMINI_API_KEY'):
        info.append('⚠️ لم يفحصه الذكاء هذه المرّة، فحكمت فيه القواعد.')
    tail = []
    if it['text'].rstrip().endswith('…') and not it.get('links'):
        tail.append('✂️ النصّ مقتطع في المصدر، افتح الرابط لقراءته كاملًا.')
    tail.append(f"🔗 {e(it['url'])}")

    def build(n, hint):
        return '\n\n'.join(x for x in [head, e(excerpt(it['text'], n))] + info + ([e(hint)] if hint else []) + tail if x)

    hint = res['hint'] or ''
    for n in (900, 500, 200):
        msg = build(n, hint)
        if len(msg) <= limit:
            return msg
    while len(msg) > limit and len(hint) > 1:
        hint = hint[: len(hint) * 3 // 4].rstrip()
        msg = build(200, hint + ' …')
    return msg


def api(cfg, method):
    return f"https://api.telegram.org/bot{cfg['bot_token']}/{method}"


def fmt_quiet(res):
    """رسالة صامتة: منشورٌ كانت القواعد سترسله ورفضه الحَكَم، للمراجعة."""
    it, e = res['item'], html.escape
    return '\n\n'.join([
        f"🔕 <b>للمراجعة:</b> رفضه الحَكَم، وكانت القواعد سترسله.",
        f"🏛 {e(res['src'])}" + (' — ' + it['date'].astimezone().strftime('%Y-%m-%d') if it.get('date') else ''),
        e(excerpt(it['text'], 300)) or '🖼 ' + e(res['ai'].get('image_text') or ''),
        f"🤖 {e(res['ai'].get('reason', ''))}",
        f"🔗 {e(it['url'])}"])


def tg_send(cfg, text, parse_mode='HTML', silent=False):
    data = {'chat_id': cfg['chat_id'], 'text': text, 'disable_web_page_preview': 'true'}
    if silent:
        data['disable_notification'] = 'true'
    if parse_mode:
        data['parse_mode'] = parse_mode
    for _ in range(4):
        try:
            j = requests.post(api(cfg, 'sendMessage'), timeout=30, data=data).json()
        except Exception as ex:
            log.warning('send: %s', ex)
            time.sleep(5)
            continue
        if j.get('ok'):
            time.sleep(1.2)
            return True
        ra = (j.get('parameters') or {}).get('retry_after')
        if not ra:
            log.error('send failed: %s', j)
            if parse_mode and j.get('error_code') == 400:
                # رفض تيلغرام التنسيق أو الطول: تُرسل نصًّا خالصًا بدل أن تضيع
                return tg_send(cfg, html.unescape(re.sub(r'<[^>]+>', '', text))[:4096], None, silent)
            return False
        time.sleep(ra + 1)
    return False


def ensure_chat_id(cfg):
    if cfg.get('chat_id'):
        return True
    j = requests.get(api(cfg, 'getUpdates'), timeout=30).json()
    for u in reversed(j.get('result', [])):
        ch = (u.get('message') or {}).get('chat') or {}
        if ch.get('type') == 'private':
            cfg['chat_id'] = ch['id']
            save_json(CFG_PATH, cfg)
            return True
    print('لم أجد محادثة مع البوت: افتحه في تيلغرام واضغط Start ثم أعد التشغيل.')
    log.error('no chat_id yet')
    return False


# ------------------------------------------------------------------ فيسبوك
class FBLoggedOut(Exception):
    pass


FB_RECENT = base64.b64encode(json.dumps(
    {'recent_posts:0': json.dumps({'name': 'recent_posts', 'args': ''})}).encode()).decode()
FB_JS = """els => els.filter(e => !e.parentElement.closest('div[role=article]')).map(e => [
  e.innerText,
  (Array.from(e.querySelectorAll('a[href]')).map(a => a.href)
     .find(h => /\\/posts\\/|story_fbid|\\/permalink\\/|\\/videos\\/|\\/photos?\\//.test(h)) || '').split('?__cft__')[0]
])"""


FB_DATE = re.compile(rf'(\d{{1,2}}) ({MONTH_RE}) (\d{{4}})')


def fb_key(text):
    keep = [l for l in text.splitlines() if len(l.strip()) > 25 and 'منذ' not in l and 'الساعة' not in l]
    body = re.sub(r'\d', '', norm(' '.join(keep) or text))
    return hashlib.sha1(body[:150].encode('utf-8')).hexdigest()[:12]


def fb_date(text):
    m = FB_DATE.search(norm(text[:300]))
    d = mkdate(int(m[3]), MONTHS[m[2]], int(m[1])) if m else None
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc) if d else None


def fb_context(p, headless):
    return p.chromium.launch_persistent_context(
        str(FB_PROFILE), channel=browser_channel(), headless=headless, locale='ar',
        viewport=None if not headless else {'width': 1280, 'height': 2200},
        no_viewport=not headless)


def fb_collect(fb):
    from playwright.sync_api import sync_playwright
    targets = [(p['name'], p['url'], p.get('assume_hama', True)) for p in fb.get('pages', []) if p.get('url')]
    targets += [(f'فيسبوك — بحث: {q}', 'https://www.facebook.com/search/posts?q=' + quote(q)
                 + '&filters=' + quote(FB_RECENT), False) for q in fb.get('searches', [])]
    out = []
    with sync_playwright() as p:
        ctx = fb_context(p, fb.get('headless', True))
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            for name, url, assume in targets:
                page.goto(url, wait_until='domcontentloaded', timeout=60000)
                page.wait_for_timeout(6000)
                if '/login' in page.url or page.locator('input[name="pass"]').count():
                    raise FBLoggedOut()
                got = {}
                for step in range(fb.get('scrolls', 4) + 1):
                    for btn in page.locator('div[role="button"]:text-is("عرض المزيد"), '
                                            'div[role="button"]:text-is("See more")').all()[:15]:
                        try:
                            btn.click(timeout=1500)
                        except Exception:
                            pass
                    page.wait_for_timeout(1200)
                    # فيسبوك يفرّغ المنشورات البعيدة عن الشاشة، فنجمعها بعد كلّ تمرير
                    for text, href in page.eval_on_selector_all('div[role="article"]', FB_JS):
                        if len(text) > 60:
                            href = re.sub(r'[?&]__(cft|tn)__.*$', '', href or '')
                            m = re.search(r'/posts/(\w+)|story_fbid=(\w+)|[?&]fbid=(\d+)|/permalink/(\d+)', href)
                            k = next(g for g in m.groups() if g) if m else fb_key(text)
                            if k not in got or len(text) > len(got[k]['text']):
                                got[k] = {'id': 'fb:' + k, 'key': k, 'text': text, 'date': fb_date(text),
                                          'url': href or url, 'links': [], 'source_name': name,
                                          'assume_hama': assume, 'strict_gov': not assume}
                    page.mouse.wheel(0, 2500)
                    page.wait_for_timeout(2500)
                out += got.values()
                page.wait_for_timeout(random.randint(3000, 8000))
        finally:
            ctx.close()
    return out


def fb_login():
    from playwright.sync_api import sync_playwright
    FB_PROFILE.mkdir(exist_ok=True)
    with sync_playwright() as p:
        ctx = fb_context(p, False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto('https://www.facebook.com/')
        print('سجّل دخولك بالحساب الثانويّ في النافذة التي فُتحت، ثم أغلقها.')
        try:
            page.wait_for_event('close', timeout=0)
        except Exception:
            pass
        try:
            ctx.close()
        except Exception:
            pass


def fb_find(q):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = fb_context(p, True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto('https://www.facebook.com/search/pages?q=' + quote(q), timeout=60000)
        page.wait_for_timeout(7000)
        rows = page.eval_on_selector_all('div[role="main"] a[href^="https://www.facebook.com/"]',
                                         'els => els.map(a => [a.innerText.trim(), a.href.includes("profile.php") ? a.href.split("&")[0] : a.href.split("?")[0]])')
        seen = set()
        for t, h in rows:
            if t and h not in seen and '/search/' not in h and len(t) > 3:
                seen.add(h)
                print(f'{t}\t{h}')
        ctx.close()


# ------------------------------------------------------------------ التشغيل
def load_json(p, default):
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return default


def save_json(p, data):
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(p)


def save_state(state, touched):
    """يحفظ الحالة. ما ظهر في هذا الفحص (touched) يُنقل إلى آخر قائمة seen، فلا يُقصّ منها
    ما زال منشورًا (وظيفة طويلة الأجل في بوّابة التوظيف مثلًا) فيُرسل من جديد."""
    seen, olds = state['seen_set'], set(state['seen'])
    order = [x for x in state['seen'] if x not in touched]
    order += [x for x in touched if x in seen]
    order += [x for x in seen if x not in olds and x not in touched]
    out = {k: v for k, v in state.items() if k not in ('seen_set', 'codes_set')}
    out['seen'] = order[-8000:]
    oldc = set(state['codes'])
    out['codes'] = (state['codes'] + sorted(c for c in state['codes_set'] if c not in oldc))[-2000:]
    save_json(STATE_PATH, out)


def note_fail(cfg, state, name, err):
    n = state['fail'].get(name, 0) + 1
    state['fail'][name] = n
    log.warning('source %s failed (%d): %s', name, n, err)
    if n == 3:
        tg_send(cfg, f'⚠️ المصدر «{html.escape(name.split("|")[0])}» لم يعمل في آخر 3 محاولات. سأستمرّ في المحاولة.')


def run():
    cfg = load_json(CFG_PATH, None)
    if not cfg or not ensure_chat_id(cfg):
        return
    state = load_json(STATE_PATH, {})
    state.setdefault('seen', []); state.setdefault('codes', []); state.setdefault('fail', {})
    first = not state.get('first_done')
    state['seen_set'], state['codes_set'] = set(state['seen']), set(state['codes'])
    days = cfg.get('first_run_lookback_days', 30) if first else cfg.get('lookback_days', 5)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    # found: ما سيُرسل. pending: معرّفاته وبصماته، حتّى لا يُقبل مرّتين في هذا الفحص.
    # touched: كلّ ما ظهر في هذا الفحص (انظر save_state).
    # المرفوض يُسجَّل في seen فورًا، أمّا المقبول فلا يُسجَّل إلّا بعد وصول رسالته، فما لم يصل يُعاد في الفحص التالي.
    found, pending, touched = [], set(), {}

    def consider(items, src):
        for it in items:
            touched[it['id']] = None
            if it['id'] in state['seen_set'] or it['id'] in pending:
                continue
            s = dict(src, assume_hama=it.get('assume_hama', src.get('assume_hama')))
            if it.get('direct'):
                if expired(it['direct'].get('deadline'), it.get('date')):
                    state['seen_set'].add(it['id'])
                    continue
                pending.add(it['id'])
                found.append(dict(it['direct'], item=it, src=src['name'], marks={it['id']}, codes=set()))
                continue
            try:
                res = evaluate(it, s, state, pending)
            except JudgeUnavailable:
                log.info('judge unavailable, retry next scan: %s', it['id'])
                continue
            except Exception as e:
                log.exception('evaluate %s: %s', it['id'], e)
                continue  # يُعاد الحكم عليه في الفحص التالي
            if res:
                res['marks'].add(it['id'])
                pending.update(res['marks'], res['codes'])
                found.append(res)
            else:
                state['seen_set'].add(it['id'])

    for src in cfg.get('sources', []):
        if not src.get('enabled', True):
            continue
        try:
            items = {'telegram': collect_tg, 'rss': collect_rss, 'tawzeef': collect_tawzeef,
                     'webpage': collect_webpage}[src['type']](src, cutoff, state['seen_set'])
            state['fail'][src['name'] + '|' + src.get('url', src.get('channel', ''))] = 0
        except Exception as e:
            note_fail(cfg, state, src['name'] + '|' + src.get('url', src.get('channel', '')), e)
            continue
        consider(items, src)

    fb = cfg.get('facebook', {})
    if fb.get('enabled') and FB_PROFILE.exists() and (fb.get('pages') or fb.get('searches')):
        try:
            consider(fb_collect(fb), {'name': 'فيسبوك'})
            state['fail']['فيسبوك'] = 0
            state['fb_logged_out'] = False
        except FBLoggedOut:
            if not state.get('fb_logged_out'):
                tg_send(cfg, '⚠️ خرج حساب فيسبوك من الجلسة. شغّل الملفّ fb-login.cmd وسجّل الدخول مجدّدًا.')
            state['fb_logged_out'] = True
        except Exception as e:
            note_fail(cfg, state, 'فيسبوك', e)

    far = datetime(1970, 1, 1, tzinfo=timezone.utc)
    found.sort(key=lambda r: r['item'].get('date') or far)
    sent = failed = 0
    for r in found:
        if tg_send(cfg, fmt_quiet(r), silent=True) if r.get('quiet') else tg_send(cfg, fmt(r)):
            sent += 1
            state['seen_set'].update(r['marks'])
            state['codes_set'].update(r['codes'])
            save_state(state, touched)  # بعد كلّ رسالة: إن توقّف التشغيل في منتصفه لم يتكرّر ما وصل
            log.info('%s: %s | %s', 'quiet' if r.get('quiet') else 'sent', r['src'], r['item']['url'])
        else:
            failed += 1
            log.error('not sent, will retry next scan: %s | %s', r['src'], r['item']['url'])
    if first:
        tg_send(cfg, f'✅ بدأ تتبّع الوظائف الحكومية في حماة. أرسلت {sent} إعلانًا ما زال باب التقديم فيه '
                     f'مفتوحًا من آخر {days} يومًا. من الآن سأرسل الجديد فقط.')
    # scan_failing يكتبه ملفّ التشغيل حين يفشل فحص وينبّه، فأوّل فحص ناجح بعده يخبر بعودته.
    if state.get('scan_failing') and tg_send(cfg, '✅ عاد فحص الوظائف يعمل.'):
        state.pop('scan_failing')
    # DUE_ERROR: لم يفهم due.py سطر المواعيد، فصارت كلّ نبضة تفحص احتياطًا. يُنبَّه مرّةً واحدة.
    if os.environ.get('DUE_ERROR'):
        if not state.get('due_error') and tg_send(
                cfg, '⚠️ سطر المواعيد SLOTS في ملفّ التشغيل لا يُفهم، فصار البوت يفحص كلّ ساعة احتياطًا. '
                     'صحّحه بصيغة مثل: 09:07 12:07 15:07 18:07'):
            state['due_error'] = True
    else:
        state.pop('due_error', None)
    state['first_done'] = True
    state['last_run'] = datetime.now().isoformat(timespec='seconds')
    save_state(state, touched)
    log.info('run done: %d candidates, %d sent, %d failed', len(found), sent, failed)
    print(f'تمّ: {sent} رسالة' + (f'، وتعذّر إرسال {failed} (يُعاد في الفحص التالي).' if failed else '.'))


if __name__ == '__main__':
    a = sys.argv[1:]
    if a[:1] == ['--fb-login']:
        fb_login()
    elif a[:1] == ['--fb-find'] and len(a) > 1:
        fb_find(a[1])
    elif a[:1] == ['--test']:
        c = load_json(CFG_PATH, None)
        if c and ensure_chat_id(c):
            ok = tg_send(c, '✅ بوت الوظائف يعمل.')
            print('ok' if ok else 'failed')
            sys.exit(0 if ok else 1)
        sys.exit(1)
    else:
        run()
