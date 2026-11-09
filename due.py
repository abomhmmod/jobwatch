# -*- coding: utf-8 -*-
"""
هل حان موعد فحص؟

  python due.py 09:07 18:07

كلّ تشغيل على GitHub «نبضة»، والنبضات تأتي كلّ ساعة تقريبًا. هذا الملفّ يقرّر هل تفحص النبضة:
يخرج بالرمز 0 (افحص) إن مرّ أحد المواعيد اليوميّة منذ آخر فحص ناجح (last_run في state.json)،
وبالرمز 3 (لا تفحص) إن لم يمرّ. فإن تأخّرت نبضة أو سقطت، فحصت النبضة التي بعدها ولم يضِع الموعد.
وأيّ رمز غيرهما خطأ (سطر مواعيد لا يُفهم مثلًا)، فيفحص ملفّ التشغيل احتياطًا وينبّه.
المواعيد بتوقيت الجهاز (المتغيّر TZ). وبلا مواعيد يفحص دائمًا.
"""
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

STATE_PATH = Path(__file__).resolve().parent / 'state.json'
NOT_DUE = 3
SLOT_RE = r'(?<![\d:])(\d{1,2}):(\d{2})(?::\d{2})?(?!\d)'


def parse_slots(argv):
    """المواعيد من سطر SLOTS، مثل «09:07 18:07» (وتُقبل الفاصلة بينها والثواني بعدها)."""
    text = ' '.join(argv)
    slots = sorted({(int(h), int(m)) for h, m in re.findall(SLOT_RE, text)})
    if re.sub(SLOT_RE + r'|[\s,،]+', '', text) or any(h > 23 or m > 59 for h, m in slots):
        raise ValueError(f'سطر المواعيد لا يُفهم: {text!r}')
    return slots


def latest_slot(now, slots):
    """آخر موعد حلّ حتّى الآن، من مواعيد اليوم أو أمس."""
    passed = [datetime(d.year, d.month, d.day, h, m)
              for d in (now.date(), now.date() - timedelta(days=1)) for h, m in slots]
    return max(t for t in passed if t <= now)


def last_scan(path=STATE_PATH):
    try:
        return datetime.fromisoformat(json.loads(path.read_text(encoding='utf-8'))['last_run'])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def is_due(now, slots, last):
    return not slots or last is None or last < latest_slot(now, slots)


def main(argv):
    slots = parse_slots(argv)
    now, last = datetime.now(), last_scan()
    due = is_due(now, slots, last)
    slot = f'{latest_slot(now, slots):%Y-%m-%d %H:%M}' if slots else '-'
    print(f'الآن {now:%Y-%m-%d %H:%M} | آخر فحص {last or "-"} | آخر موعد حلّ {slot} | '
          + ('حان موعد الفحص.' if due else 'لم يحِن موعد جديد.'))
    return 0 if due else NOT_DUE


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as e:
        print(f'::error::due.py: {e}')
        sys.exit(2)
