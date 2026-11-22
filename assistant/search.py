"""
Live web search with Tavily's FREE plan.

Safety rules built in:
  * Never searches unless a key is configured (no key = feature off, no errors).
  * Same question within 24 h is answered from the cache (no credit used).
  * Stops at TAVILY_MONTHLY_LIMIT so the free plan can never run out or bill you.
  * Personal details are removed from the query before it leaves your server.
  * Official sites (universities, governments, DAAD, ...) are ranked first and
    labelled; everything else is labelled "third-party".
  * Web pages are untrusted text: pages that try to give the AI orders are dropped.
"""

import hashlib
import json
import re
import urllib.error
import urllib.request
from datetime import timedelta
from urllib.parse import urlparse

from django.conf import settings
from django.utils import timezone

from .models import SearchLog

MAX_RESULTS = 5


class SearchUnavailable(Exception):
    """Web search cannot be used right now. `reason` is a short code."""

    def __init__(self, reason, message=''):
        super().__init__(message or reason)
        self.reason = reason
        self.message = message or reason


# ------------------------------------------------------------ official sites
OFFICIAL_SUFFIXES = (
    '.edu', '.ac.uk', '.gov', '.gov.uk', '.gc.ca', '.edu.au', '.europa.eu',
)
OFFICIAL_DOMAINS = {
    'daad.de', 'uni-assist.de', 'auswaertiges-amt.de', 'make-it-in-germany.com',
    'study-in-germany.de', 'tum.de', 'rwth-aachen.de', 'kit.edu', 'ucl.ac.uk',
    'chevening.org', 'britishcouncil.org', 'ucas.com', 'canada.ca', 'educanada.ca',
    'travel.state.gov', 'studyinthestates.dhs.gov', 'fulbrightonline.org',
    'ielts.org', 'ets.org', 'pearsonpte.com', 'bb.org.bd', 'gov.uk',
}
GERMAN_UNI_RE = re.compile(r'(^|\.)(uni|tu|fh|hs|th)-[a-z0-9-]+\.de$')


def _host(url):
    host = (urlparse(url).hostname or '').lower()
    return host[4:] if host.startswith('www.') else host


def classify(url, extra_hosts=()):
    """Return "official" or "third_party"."""
    host = _host(url)
    if not host:
        return 'third_party'
    if host in OFFICIAL_DOMAINS or any(host.endswith('.' + d) for d in OFFICIAL_DOMAINS):
        return 'official'
    if host.endswith(OFFICIAL_SUFFIXES) or GERMAN_UNI_RE.search(host):
        return 'official'
    for known in extra_hosts:
        if host == known or host.endswith('.' + known):
            return 'official'
    return 'third_party'


def stored_official_hosts():
    """Hosts of the official links already in the PathWise database."""
    from opportunities.models import Opportunity
    hosts = set()
    for url in Opportunity.objects.filter(is_active=True).values_list('source_url', flat=True):
        host = _host(url)
        if host:
            hosts.add(host)
    return hosts


# ------------------------------------------------------------------- privacy
EMAIL_RE = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')
LONG_NUMBER_RE = re.compile(r'\+?\d[\d\s().-]{6,}\d')
URL_RE = re.compile(r'https?://\S+')
SECRET_RE = re.compile(r'\b(tvly-[A-Za-z0-9_-]{8,}|sk-[A-Za-z0-9_-]{16,}|AIza[0-9A-Za-z_-]{20,})\b')


def scrub_query(text):
    """Remove personal details before a question is sent to the web."""
    text = EMAIL_RE.sub(' ', text or '')
    text = URL_RE.sub(' ', text)
    text = SECRET_RE.sub(' ', text)
    text = LONG_NUMBER_RE.sub(' ', text)
    return re.sub(r'\s+', ' ', text).strip()[:300]


# ------------------------------------------------------ prompt-injection guard
INJECTION_RE = re.compile(
    r'ignore (all |any |the )?(previous|above|prior|earlier) (instructions|prompts?)'
    r'|disregard (all |any |the )?(previous|above|prior)'
    r'|forget (all |your )?(previous|prior) instructions'
    r'|reveal (your |the )?(system )?prompt'
    r'|you are now\b|new instructions:|\bsystem prompt\b|<\s*/?\s*system\s*>',
    re.IGNORECASE,
)


def looks_like_injection(text):
    return bool(INJECTION_RE.search(text or ''))


# -------------------------------------------------------------------- usage
def _month_start():
    now = timezone.now()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def usage_this_month():
    return SearchLog.objects.filter(created_at__gte=_month_start()).count()


def search_configured():
    return bool(settings.TAVILY_API_KEY)


def status():
    return {
        'configured': search_configured(),
        'used_this_month': usage_this_month(),
        'monthly_limit': settings.TAVILY_MONTHLY_LIMIT,
    }


# -------------------------------------------------------------------- search
def _post(payload):
    """Call Tavily. Separate function so tests can replace it."""
    request = urllib.request.Request(
        f'{settings.TAVILY_BASE_URL}/search',
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'User-Agent': 'PathWise/1.0 (study-abroad planner)',   # Python's default one is often blocked
            'Authorization': f'Bearer {settings.TAVILY_API_KEY}',
        },
        method='POST',
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode('utf-8'))


def search(query):
    """
    Return {"results": [...], "cached": bool}.
    Each result: title, url, snippet, published, kind ("official" | "third_party"), host.
    Raises SearchUnavailable when search cannot or should not run.
    """
    query = scrub_query(query)
    if len(query) < 3:
        raise SearchUnavailable('empty_query', 'The question has nothing safe to search for.')
    if not search_configured():
        raise SearchUnavailable('not_configured', 'Web search is switched off (no TAVILY_API_KEY).')

    digest = hashlib.sha256(query.lower().encode('utf-8')).hexdigest()
    cutoff = timezone.now() - timedelta(hours=settings.SEARCH_CACHE_HOURS)
    cached = SearchLog.objects.filter(query_hash=digest, created_at__gte=cutoff).first()
    if cached:
        return {'results': cached.results, 'cached': True}

    if usage_this_month() >= settings.TAVILY_MONTHLY_LIMIT:
        raise SearchUnavailable(
            'limit_reached',
            'The free web-search allowance for this month is used up, so live search is paused.',
        )

    try:
        data = _post({
            'query': query,
            'search_depth': 'basic',
            'max_results': 8,
            'include_answer': False,
            'include_raw_content': False,
        })
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise SearchUnavailable('error', 'Live web search is not reachable right now.')

    known = stored_official_hosts()
    results = []
    for item in data.get('results', []):
        url = str(item.get('url', ''))
        if not url.startswith(('http://', 'https://')):
            continue
        text = f"{item.get('title', '')} {item.get('content', '')}"
        if looks_like_injection(text):
            continue
        results.append({
            'title': str(item.get('title', ''))[:200] or _host(url),
            'url': url,
            'snippet': re.sub(r'\s+', ' ', str(item.get('content', ''))).strip()[:700],
            'published': item.get('published_date') or None,
            'kind': classify(url, known),
            'host': _host(url),
        })

    results.sort(key=lambda r: 0 if r['kind'] == 'official' else 1)   # stable: keeps relevance order
    results = results[:MAX_RESULTS]

    SearchLog.objects.create(query_hash=digest, query=query, results=results)
    return {'results': results, 'cached': False}
