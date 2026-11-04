# -*- coding: utf-8 -*-
"""تشخيص مصادر RSS التي ترجع 200 بلا عناصر، مع بدائل محتملة."""
import re, requests
from urllib.parse import quote
UA = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36',
      'Accept': 'application/rss+xml, application/xml;q=0.9, */*;q=0.8', 'Accept-Language': 'ar,en;q=0.8'}
urls = [
 'https://sana.sy/?s=شواغر&feed=rss2', 'https://sana.sy/feed/', 'https://www.sana.sy/?s=شواغر&feed=rss2',
 'https://sana.sy/search/شواغر/feed/rss2/', 'https://sana.sy/wp-json/wp/v2/posts?search=شواغر&per_page=5',
 'https://fedaa.alwehda.gov.sy/?s=إعلان&feed=rss2', 'https://fedaa.alwehda.gov.sy/feed/',
 'https://fedaa.alwehda.gov.sy/wp-json/wp/v2/posts?search=إعلان&per_page=5',
 'https://news.google.com/rss/search?q=' + quote('site:sana.sy شواغر') + '&hl=ar&gl=SY&ceid=SY:ar',
 'https://news.google.com/rss/search?q=' + quote('site:fedaa.alwehda.gov.sy إعلان') + '&hl=ar&gl=SY&ceid=SY:ar',
]
for u in urls:
    try:
        r = requests.get(u, headers=UA, timeout=30)
        body = r.text
        print(f"{r.status_code} | {r.headers.get('content-type')} | len={len(body)} | items={len(re.findall(r'<item\\b', body))} | json_ids={body.count('\"id\":')} | {r.url}")
        if not re.search(r'<item\b', body) and '"id":' not in body:
            print('   >>', re.sub(r'\s+', ' ', body[:400]))
        print('   server:', r.headers.get('server'), '| cf-ray:', r.headers.get('cf-ray'))
    except Exception as e:
        print('ERR', type(e).__name__, str(e)[:150], u)
