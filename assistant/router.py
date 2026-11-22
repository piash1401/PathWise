"""
Deterministic router: decides WHICH PathWise tools to run for a message.

A small local model is not reliable at choosing tools by itself, so plain,
testable rules decide instead. The model only writes the final answer from
the facts these tools collect.
"""

import re
from dataclasses import dataclass, field

from opportunities.models import Opportunity

COUNTRY_ALIASES = {
    'Germany': ['germany', 'german', 'deutschland'],
    'UK': ['uk', 'u.k.', 'britain', 'british', 'england', 'scotland', 'united kingdom'],
    'Canada': ['canada', 'canadian'],
    'USA': ['usa', 'u.s.a', 'u.s.', 'united states', 'america', 'american'],
}

STUDY_WORDS = (
    'university', 'universities', 'college', 'master', "master's", 'masters', 'msc', 'program',
    'programme', 'programs', 'course', 'admission', 'admissions', 'apply', 'application',
    'scholarship', 'scholarships', 'tuition', 'visa', 'ielts', 'toefl', 'pte', 'sop',
    'statement of purpose', 'cgpa', 'gpa', 'intake', 'study abroad', 'abroad', 'semester',
    'blocked account', 'daad', 'chevening', 'fulbright', 'commonwealth', 'cost of living',
    'recommendation letter', 'lor', 'gre',
)

# Words that mean "this may have changed recently, check the web".
CURRENT_WORDS = (
    'latest', 'current', 'currently', 'recent', 'recently', 'news', 'today', 'right now',
    'this year', 'this month', 'new rule', 'new rules', 'announced', 'updated', 'update',
    'deadline', 'deadlines', 'intake', 'tuition', 'fee', 'fees', 'requirement', 'requirements',
    'ranking', 'rankings', 'scholarship', 'scholarships', 'opens', 'closing', 'closes',
    '2026', '2027', '2028', 'visa', 'minimum wage', 'exchange rate',
)

RECOMMEND_PATTERNS = (
    r'\bwhich (universit|program|course|college)', r'\brecommend', r'\bsuggest',
    r'\bwhere should i (apply|study)', r'\bbest (universit|program|course|option)s? for me',
    r'\bshortlist', r'\bmatch(es)? for me\b', r'\bwhat (universit|program)s? (can|should)',
)
COST_PATTERNS = (
    r'\bcost', r'\bbudget', r'\bafford', r'\bexpens', r'\bhow much\b', r'\bfunding gap',
    r'\bpart[- ]?time', r'\bliving (cost|expense)', r'\bproof of funds', r'\bblocked account',
    r'\bfinance', r'\bmoney\b', r'\btaka\b|\bbdt\b|৳',
)
ELIGIBILITY_PATTERNS = (
    r'\beligib', r'\bcan i (apply|get in|get admitted)', r'\bdo i (meet|qualify)',
    r'\bam i (qualified|eligible)', r'\bchance of\b', r'\bmeet the requirement',
    r'\bwhich requirements',
)
TRACKER_PATTERNS = (
    r'\bmy applications?\b', r'\bapplication tracker\b', r'\bmy tracker\b', r'\bnext steps?\b',
    r'\bwhat should i do next\b', r'\bmy deadlines?\b', r'\bwhat(\'s| is) left\b',
    r'\bdocuments? (do i|i still) need\b', r'\bmy checklist\b',
)
COMPARE_PATTERNS = (r'\bcompare\b', r'\bversus\b', r'\bvs\.?\b', r'\bdifference between\b')
PROFILE_PATTERNS = (
    r'\bmy (profile|cgpa|gpa|ielts|budget|score|grades?|background|degree)\b',
    r'\bfor me\b', r'\bi have\b', r'\bi am\b', r"\bi'm\b", r'\bshould i\b',
)

GENERIC_INSTITUTION_WORDS = {
    'university', 'universität', 'of', 'the', 'technical', 'institute', 'college', 'and',
    'technology', 'state', 'school',
}
INSTITUTION_ALIASES = {
    'tum': 'munich', 'rwth': 'aachen', 'ucl': 'london', 'asu': 'arizona',
    'uoft': 'toronto', 'u of t': 'toronto', 'gatech': 'georgia', 'georgia tech': 'georgia',
}


@dataclass
class Plan:
    study: bool = False
    web: bool = False
    kb: bool = False
    profile: bool = False
    recommend: bool = False
    cost: bool = False
    eligibility: bool = False
    tracker: bool = False
    compare: bool = False
    countries: list = field(default_factory=list)
    opportunities: list = field(default_factory=list)

    @property
    def uses_tools(self):
        return any([self.recommend, self.cost, self.eligibility, self.tracker, self.compare])


def _has(text, patterns):
    return any(re.search(p, text) for p in patterns)


def _contains_word(text, word):
    return re.search(rf'(?<![a-z]){re.escape(word)}(?![a-z])', text) is not None


def find_countries(text):
    text = text.lower()
    found = []
    for country, aliases in COUNTRY_ALIASES.items():
        if any(_contains_word(text, a) for a in aliases):
            found.append(country)
    return found


def find_opportunities(text, limit=2):
    """Find stored programs the message talks about (by institution, then title)."""
    text = text.lower()
    expanded = text
    for alias, target in INSTITUTION_ALIASES.items():
        if _contains_word(text, alias):
            expanded += ' ' + target

    matches = []
    for opp in Opportunity.objects.filter(is_active=True):
        tokens = [
            t for t in re.findall(r'[a-zäöüß]+', opp.institution.lower())
            if t not in GENERIC_INSTITUTION_WORDS and len(t) >= 3
        ]
        if not tokens or not all(_contains_word(expanded, t) for t in tokens):
            continue
        title_tokens = [t for t in re.findall(r'[a-z]+', opp.title.lower()) if len(t) > 3]
        score = sum(1 for t in title_tokens if _contains_word(text, t))
        matches.append((score, opp))

    matches.sort(key=lambda m: -m[0])
    chosen, seen = [], set()
    for _, opp in matches:
        if opp.institution in seen and len(matches) > limit:
            continue
        seen.add(opp.institution)
        chosen.append(opp)
    return chosen[:limit]


def detect(message, previous_user_text='', authenticated=True):
    """Build a Plan for this message. A short follow-up borrows context from the previous one."""
    text = message.lower()
    words = re.findall(r'\w+', text)

    # "What about its tuition?" has no keywords of its own: look at the previous question too.
    context = text
    if len(words) <= 7 and previous_user_text:
        context = f'{previous_user_text.lower()} {text}'

    plan = Plan()
    plan.study = any(_contains_word(context, w) for w in STUDY_WORDS) or bool(find_countries(context))
    plan.countries = find_countries(context)
    plan.opportunities = find_opportunities(context)
    if plan.opportunities:
        plan.study = True

    plan.recommend = authenticated and _has(text, RECOMMEND_PATTERNS)
    plan.eligibility = authenticated and _has(text, ELIGIBILITY_PATTERNS)
    plan.tracker = authenticated and _has(text, TRACKER_PATTERNS)
    plan.compare = _has(text, COMPARE_PATTERNS) and len(plan.opportunities) >= 2
    plan.cost = _has(text, COST_PATTERNS) and (plan.study or bool(plan.countries))

    plan.profile = authenticated and (
        plan.recommend or plan.eligibility or plan.cost or _has(text, PROFILE_PATTERNS) and plan.study
    )
    plan.kb = plan.study

    # Live web search only when the question is about things that change.
    explicit_current = any(_contains_word(text, w) for w in CURRENT_WORDS if ' ' not in w) or \
        any(w in text for w in CURRENT_WORDS if ' ' in w)
    if plan.study:
        plan.web = explicit_current
    else:
        plan.web = any(_contains_word(text, w) for w in
                       ('latest', 'news', 'today', 'current', 'currently', 'recent', 'recently', 'right now'))
    return plan
