# -*- coding: utf-8 -*-
"""
JobWatch — متتبّع الوظائف الحكومية في حماة، يرسل الجديد إلى بوت تيلغرام.

  python jobwatch.py              تشغيل عاديّ (تشغّله جدولة المهام)
  python jobwatch.py --test       رسالة تجريبية إلى البوت
  python jobwatch.py --fb-login   نافذة فيسبوك لتسجيل الدخول مرّة واحدة
  python jobwatch.py --fb-find "اسم"   البحث عن روابط صفحات فيسبوك
"""
import base64, email.utils, hashlib, html, io, json, logging, random, re, sys, time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

BASE = Path(__file__).resolve().parent
CFG_PATH, STATE_PATH = BASE / 'config.json', BASE / 'state.json'
FB_PROFILE = BASE / 'fb_profile'
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/130.0 Safari/537.36 Edg/130.0'}
logging.basicConfig(filename=BASE / 'jobwatch.log', level=logging.INFO, encoding='utf-8',
                    format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('jobwatch')
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

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
LOW = R(r'عامل نظافة|عمال نظافة|عامل خدمات|سائق|سائقين|حارس|حراس|مستخدم|مستخدمين|عتالة|الفئة الرابعة|الفئة الخامسة')
UNI = R(r'إجازة جامعية|إجازة في|جامعي|الفئة الأولى|ماجستير|دكتوراه|دبلوم دراسات عليا|مدرس|معلم')
ALL_GOV = R(r'(جميع|كافة|كل) ال?محافظات|المحافظات (كافة|السورية|جميعها)|جميع أنحاء')
OTHER_GOV = R(r'دمشق|حمص|حلب|إدلب|اللاذقية|طرطوس|القنيطرة|درعا|السويداء|دير الزور|الحسكة|الرقة')
JOB = R(r'شواغر|شاغر|فرص عمل|فرصة عمل|فرصة وظيفية|طلبات التوظيف|باب التعاقد|باب التوظيف|للتعاقد مع|التعاقد مع'
        r'|حاجتها (إلى|ل)ـ? ?(موظف|عامل|مدرس|معلم|حملة|خريج|كوادر|اختصاص)')
APPLY = R(r'التقدم|التقديم|تقديم الطلبات|طلب التوظيف|الراغبين|رابط|مراجعة|استقبال الطلبات|بطاقة الشاغر')
ANNOUNCE = R(r'تعلن|يعلن|أعلنت|أعلن|إعلان|تدعو|يدعو|دعت')
CONTEST, CONTEST_CTX = R(r'مسابقة'), R(r'لشغل|لتعيين|للتعاقد|لاختيار|الفئة')
EXCLUDE = R(r'أسماء الناجحين|أسماء المقبولين|نتائج|مناقصة|عروض أسعار|استدراج عروض|تطوعي|تطوعية|مزاد|استئجار')
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
SCOPE_AR = {'hama': 'حماة', 'all': 'جميع المحافظات (ومنها حماة)', 'unknown': 'لم تُذكر المحافظة في النصّ'}


def scope(t):
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
DL_ANCHOR = R(r'آخر موعد|لغاية|حتى تاريخ|ينتهي التقديم|موعد أقصاه|حتى نهاية دوام')


def mkdate(y, m, d):
    try:
        return date(y, m, d)
    except ValueError:
        return None


def deadline(t):
    for m in DL_ANCHOR.finditer(t):
        s = t[m.end(): m.end() + 90]
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


# ------------------------------------------------------------------ المصادر: تيلغرام
def tg_page(channel, q=None, before=None):
    params = {k: v for k, v in (('q', q), ('before', before)) if v}
    r = requests.get(f'https://t.me/s/{channel}', params=params, headers=UA, timeout=40)
    r.raise_for_status()
    out = []
    for m in BeautifulSoup(r.text, 'html.parser').select('div.tgme_widget_message'):
        post, tx = m.get('data-post') or '', m.select_one('.tgme_widget_message_text')
        if not post or not tx:
            continue
        tm = m.select_one('a.tgme_widget_message_date time[datetime]') or m.select_one('time[datetime]')
        out.append({'id': 'tg:' + post, 'text': tx.get_text('\n'), 'url': 'https://t.me/' + post,
                    'date': datetime.fromisoformat(tm['datetime']) if tm else None,
                    'links': [a.get('href', '') for a in tx.select('a[href]')]})
    return out


def collect_tg(src, cutoff):
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


def collect_rss(src, cutoff):
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
        if src.get('split_ads') and 'اعلانات' in norm(title):
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
    return hashlib.sha1(norm(t)[:400].encode('utf-8')).hexdigest()[:12]


# ------------------------------------------------------------------ المصادر: بوابة التوظيف (tawzeefportal)
TZ_API = 'https://api.tawzeefportal.com/api/Seeker/Job/'
TZ_H = dict(UA, **{'Accept-Language': 'ar', 'TenantId': '1'})
GOV_CO = R(r'وزارة|الهيئات والمؤسسات العامة|حكوم|محافظة|مديرية|جامعة|بلدية|مجلس مدينة|الجهات العامة')
GOV_IND = {'الخدمات الحكومية', 'الإدارة المحلية والمجالس البلدية'}


def collect_tawzeef(src, cutoff):
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
def collect_webpage(src, cutoff):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(channel='msedge', headless=True)
        try:
            pg = b.new_page()
            pg.goto(src['url'], timeout=90000, wait_until='networkidle')
            pg.wait_for_timeout(4000)
            text = pg.inner_text('main') if pg.locator('main').count() else pg.inner_text('body')
        finally:
            b.close()
    if src['empty_marker'] in text:
        return []
    body = text.split(src.get('start_after', ''), 1)[-1] if src.get('start_after') else text
    return [{'id': 'page:' + sha(body), 'text': f"🆕 تغيّرت صفحة «{src['name']}» وظهر فيها محتوى جديد:\n{body}",
             'url': src['url'], 'date': datetime.now(timezone.utc), 'links': [],
             'direct': {'scope': 'hama', 'deadline': None, 'hint': None, 'kind': 'افتح الصفحة'}}]


# ------------------------------------------------------------------ ملفّ الشواغر (PDF)
def fetch_pdf_text(url):
    r = requests.get(url, headers=UA, timeout=60, allow_redirects=True)
    data = r.content
    if not data.startswith(b'%PDF'):
        m = re.search(r'/d/([\w-]{20,})', r.url) or re.search(r'[?&]id=([\w-]{20,})', r.url)
        if not m:
            return None
        data = b''
        for u, p in (('https://drive.google.com/uc', {'export': 'download', 'id': m[1]}),
                     ('https://drive.usercontent.google.com/download',
                      {'id': m[1], 'export': 'download', 'confirm': 't'})):
            data = requests.get(u, params=p, headers=UA, timeout=90).content
            if data.startswith(b'%PDF'):
                break
        if not data.startswith(b'%PDF'):
            return None
    try:
        from pypdf import PdfReader
    except ImportError:
        from PyPDF2 import PdfReader
    return '\n'.join((pg.extract_text() or '') for pg in PdfReader(io.BytesIO(data)).pages)


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
def evaluate(it, src, state):
    t = norm(it['text'])
    if not is_job(t):
        return None
    if not src.get('trusted_gov') and not is_gov(t):
        return None
    if it.get('strict_gov') and not STRONG_GOV.search(t):
        return None
    sc = scope(t)
    if sc == 'other':
        return None
    if sc == 'unknown' and src.get('assume_hama'):
        sc = 'hama'
    dl = deadline(t)
    if dl and dl < date.today():
        return None
    if not dl and it.get('date') and it['date'] < datetime.now(timezone.utc) - timedelta(days=14):
        return None  # بلا موعد معروف وقديم: غالبًا انتهى
    fp = 'fp:' + (it.get('key') or sha(it['text']))
    links = it.get('links', [])
    codes = set(re.findall(r'(?:hiring|applying)\d{6,}', it['text'] + ' ' + ' '.join(links)))
    hiring = {c for c in codes if c.startswith('hiring')}
    if fp in state['seen_set'] or hiring & state['codes_set']:
        return None
    hint, ktext = None, t
    pdf = next((l for l in links if re.search(r'bit\.ly/hiring|drive\.google\.com/file', l)), None)
    if pdf:
        try:
            txt = fetch_pdf_text(pdf)
        except Exception as e:
            log.warning('PDF %s: %s', pdf, e)
            txt = None
        if txt:
            ktext += ' ' + norm(txt)
            count, match = analyse_pdf(txt)
            if count == 0 and sc == 'unknown':
                return None
            if count == 0:
                hint = '📄 لم أجد حماة صراحةً في ملفّ الشواغر، راجعه بنفسك.'
            elif match:
                named = [m for m in dict.fromkeys(match) if m != '؟']
                hint = (f'📄 عدد الوظائف التي تشمل حماة في ملفّ الشواغر: {count}، منها {len(match)} قد تناسب شهادتك'
                        + (': ' + '، '.join(named) if named else ' (عناوينها غير مقروءة آليًّا، افتح الملفّ)'))
            else:
                hint = (f'📄 عدد الوظائف التي تشمل حماة في ملفّ الشواغر: {count}، ولم أجد بينها اختصاص '
                        'اللغة العربية أو «أيّ اختصاص» صراحةً. راجعه بنفسك.')
        else:
            hint = '📄 تعذّرت قراءة ملفّ الشواغر آليًّا، راجعه بنفسك.'
    if LOW.search(ktext) and not UNI.search(ktext):
        return None  # وظائف الفئتين الرابعة والخامسة فقط: لا تخصّ شهادته
    if dl is None and pdf is None and sc == 'unknown' and not src.get('trusted_gov'):
        return None
    state['seen_set'].add(fp)
    state['codes_set'] |= hiring
    return {'item': it, 'src': it.get('source_name') or src['name'], 'scope': sc,
            'deadline': dl, 'hint': hint, 'kind': kind(ktext)}


# ------------------------------------------------------------------ تيلغرام: الإرسال
NOISE = R(r'^(#\S+\s*)+$|^(أعجبني|تعليق|مشاركة|إرسال|عرض المزيد|See more|Like|Comment|Share)$')


def excerpt(text, n=900):
    lines = [l.strip() for l in text.splitlines()]
    lines = [l for l in lines if l and not NOISE.match(norm(l))]
    s = '\n'.join(lines)
    return s if len(s) <= n else s[:n].rsplit(' ', 1)[0] + ' …'


def fmt(res):
    it, e = res['item'], html.escape
    head = f"🏛 <b>{e(res['src'])}</b>"
    if it.get('date'):
        head += ' — ' + it['date'].astimezone().strftime('%Y-%m-%d')
    parts = [head, e(excerpt(it['text'])), f"📍 النطاق: {SCOPE_AR[res['scope']]}",
             f"📑 نوع التوظيف: {res['kind']}"]
    if res['deadline']:
        parts.append(f"⏳ آخر موعد: {res['deadline']:%d/%m/%Y}")
    if res['hint']:
        parts.append(e(res['hint']))
    if it['text'].rstrip().endswith('…') and not it.get('links'):
        parts.append('✂️ النصّ مقتطع في المصدر، افتح الرابط لقراءته كاملًا.')
    parts.append(f"🔗 {e(it['url'])}")
    return '\n\n'.join(parts)


def api(cfg, method):
    return f"https://api.telegram.org/bot{cfg['bot_token']}/{method}"


def tg_send(cfg, text):
    for _ in range(4):
        try:
            j = requests.post(api(cfg, 'sendMessage'), timeout=30, data={
                'chat_id': cfg['chat_id'], 'text': text[:4000], 'parse_mode': 'HTML',
                'disable_web_page_preview': 'true'}).json()
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
        str(FB_PROFILE), channel='msedge', headless=headless, locale='ar',
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
    found = []

    def consider(items, src):
        for it in items:
            if it['id'] in state['seen_set']:
                continue
            state['seen_set'].add(it['id'])
            s = dict(src, assume_hama=it.get('assume_hama', src.get('assume_hama')))
            if it.get('direct'):
                dl, dt = it['direct'].get('deadline'), it.get('date')
                if dl and dl < date.today():
                    continue
                if not dl and dt and dt < datetime.now(timezone.utc) - timedelta(days=14):
                    continue
                found.append(dict(it['direct'], item=it, src=src['name']))
                continue
            try:
                res = evaluate(it, s, state)
            except Exception as e:
                log.exception('evaluate %s: %s', it['id'], e)
                res = None
            if res:
                found.append(res)

    for src in cfg.get('sources', []):
        if not src.get('enabled', True):
            continue
        try:
            items = {'telegram': collect_tg, 'rss': collect_rss, 'tawzeef': collect_tawzeef,
                     'webpage': collect_webpage}[src['type']](src, cutoff)
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
    sent = 0
    for r in found:
        if tg_send(cfg, fmt(r)):
            sent += 1
            log.info('sent: %s | %s', r['src'], r['item']['url'])
    if first:
        tg_send(cfg, f'✅ بدأ تتبّع الوظائف الحكومية في حماة. أرسلت {sent} إعلانًا ما زال باب التقديم فيه '
                     f'مفتوحًا من آخر {days} يومًا. من الآن سأرسل الجديد فقط.')
    state['first_done'] = True
    old = state['seen']; olds = set(old)
    state['seen'] = (old + [x for x in state.pop('seen_set') if x not in olds])[-8000:]
    state['codes'] = list(state.pop('codes_set'))[-2000:]
    state['last_run'] = datetime.now().isoformat(timespec='seconds')
    save_json(STATE_PATH, state)
    log.info('run done: %d candidates, %d sent', len(found), sent)
    print(f'تمّ: {sent} رسالة.')


if __name__ == '__main__':
    a = sys.argv[1:]
    if a[:1] == ['--fb-login']:
        fb_login()
    elif a[:1] == ['--fb-find'] and len(a) > 1:
        fb_find(a[1])
    elif a[:1] == ['--test']:
        c = load_json(CFG_PATH, None)
        if c and ensure_chat_id(c):
            print('ok' if tg_send(c, '✅ بوت الوظائف يعمل.') else 'failed')
    else:
        run()
