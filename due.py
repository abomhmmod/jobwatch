# -*- coding: utf-8 -*-
"""
هل حان موعد فحص؟

  python due.py 09:07 18:07

كلّ تشغيل على GitHub «نبضة»، والنبضات تأتي كلّ ساعة تقريبًا. هذا الملفّ يقرّر هل تفحص النبضة:
يخرج بالرمز 0 (افحص) إن مرّ أحد المواعيد اليوميّة منذ آخر فحص ناجح (last_run في state.json)،
وبالرمز 1 (لا تفحص) إن لم يمرّ. فإن تأخّرت نبضة أو سقطت، فحصت النبضة التي بعدها ولم يضِع الموعد.
المواعيد بتوقيت الجهاز (المتغيّر TZ). وبلا مواعيد يفحص دائمًا.
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

STATE_PATH = Path(__file__).resolve().parent / 'state.json'


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
    slots = sorted(tuple(int(x) for x in a.split(':')) for a in argv)
    now, last = datetime.now(), last_scan()
    due = is_due(now, slots, last)
    slot = f'{latest_slot(now, slots):%Y-%m-%d %H:%M}' if slots else '-'
    print(f'الآن {now:%Y-%m-%d %H:%M} | آخر فحص {last or "-"} | آخر موعد حلّ {slot} | '
          + ('حان موعد الفحص.' if due else 'لم يحِن موعد جديد.'))
    return 0 if due else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
