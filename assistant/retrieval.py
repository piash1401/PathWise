"""
Source retrieval for the AI counsellor (the "R" in RAG).

We turn every verified record in the database (programs, scholarships and
country cost data) into a small text document, then rank the documents against
the student's question with TF-IDF cosine similarity. It needs no extra
libraries and every result points back to a real record and its source link.

Upgrade path: replace `retrieve()` with embeddings + pgvector (as in the
proposal) without changing the rest of the app.
"""

import math
import re
from collections import Counter

from applications.checklists import BY_COUNTRY, COMMON
from costs.models import CountryCost
from opportunities.models import Opportunity

STOPWORDS = {
    'a', 'an', 'and', 'are', 'as', 'at', 'be', 'by', 'can', 'do', 'does', 'for', 'from',
    'how', 'i', 'in', 'is', 'it', 'me', 'my', 'of', 'on', 'or', 'the', 'to', 'what',
    'when', 'which', 'with', 'will', 'you', 'your', 'should', 'need', 'much', 'many',
}

ALIASES = {
    'uk': 'united kingdom britain england',
    'usa': 'united states america us',
    'cost': 'costs fee fees tuition',
    'costs': 'cost fee fees tuition',
    'job': 'jobs work part-time parttime',
    'work': 'job jobs part-time parttime',
    'visa': 'visa permit proof funds',
}


def tokenize(text):
    words = re.findall(r"[a-z0-9][a-z0-9'-]*", (text or '').lower())
    return [w for w in words if w not in STOPWORDS and len(w) > 1]


def expand(tokens):
    expanded = list(tokens)
    for token in tokens:
        expanded.extend(tokenize(ALIASES.get(token, '')))
    return expanded


def _opportunity_document(opp, number=None):
    parts = [
        f'{opp.title} at {opp.institution}, {opp.country}.',
        opp.description,
        'Eligibility: ' + opp.eligibility_criteria,
    ]
    if opp.deadline:
        parts.append(f'Deadline: {opp.deadline.isoformat()}.')
    for source in opp.sources.all():
        parts.append(f'{source.get_fact_type_display()}: {source.source_label}. {source.notes}')

    return {
        'kind': opp.get_opportunity_type_display(),
        'title': f'{opp.title} - {opp.institution}',
        'url': opp.source_url,
        'verified_on': opp.verified_on.isoformat() if opp.verified_on else None,
        'status': opp.verification_status,
        'text': ' '.join(p for p in parts if p),
    }


def _country_documents(cost):
    """Split one country record into three focused documents (better retrieval)."""
    first_source = (cost.sources or [{}])[0]
    common = {
        'kind': 'Country guide',
        'url': first_source.get('url', ''),
        'verified_on': cost.verified_on.isoformat() if cost.verified_on else None,
        'status': 'verified' if cost.verified_on else 'pending',
    }
    return [
        {**common, 'title': f'{cost.country}: living costs, fees and insurance', 'text': ' '.join(p for p in [
            f'Studying in {cost.country}: living costs and fees.',
            f'Typical living cost is about {cost.living_per_month} {cost.currency} per month.',
            f'Health insurance is about {cost.health_insurance_per_year} {cost.currency} per year.',
            f'Visa and permit fees are about {cost.visa_fees} {cost.currency}.',
            cost.notes,
        ] if p)},
        {**common, 'title': f'{cost.country}: visa proof of funds', 'text': ' '.join(p for p in [
            f'Studying in {cost.country}: visa proof of funds, financial requirement, blocked account.',
            f'The proof of funds amount is {cost.proof_of_funds_per_year} {cost.currency} per year.',
            cost.proof_of_funds_note,
        ] if p)},
        {**common, 'title': f'{cost.country}: part-time work rules', 'text': ' '.join(p for p in [
            f'Studying in {cost.country}: part-time work, jobs, working hours, wages.',
            f'Students may work up to {cost.part_time_max_hours_per_week} hours per week during term, '
            f'at about {cost.part_time_hourly_wage} {cost.currency} per hour.',
            cost.part_time_note,
        ] if p)},
    ]


def _checklist_documents():
    common = '; '.join(label for _, label in COMMON)
    documents = []
    for country, items in BY_COUNTRY.items():
        extra = '; '.join(label for _, label in items)
        documents.append({
            'kind': 'Application checklist',
            'title': f'{country}: general application and visa documents',
            'url': '', 'verified_on': None, 'status': 'pending',
            'text': (f'General document checklist for applying to study in {country}. '
                     f'Common documents: {common}. Country-specific steps: {extra}. '
                     'This list is general; confirm it on the program and embassy pages.'),
        })
    return documents


def build_corpus():
    documents = []

    for opp in Opportunity.objects.filter(is_active=True).prefetch_related('sources'):
        documents.append(_opportunity_document(opp))

    for cost in CountryCost.objects.all():
        documents.extend(_country_documents(cost))

    documents.extend(_checklist_documents())

    return documents


def retrieve(question, top_k=4, min_score=0.08, corpus=None):
    """Return the best-matching documents, each with a score."""
    corpus = corpus if corpus is not None else build_corpus()
    query_tokens = expand(tokenize(question))

    if not corpus or not query_tokens:
        return []

    doc_tokens = [Counter(tokenize(doc['title'] + ' ' + doc['text'])) for doc in corpus]
    n_docs = len(corpus)
    doc_freq = Counter()
    for counts in doc_tokens:
        doc_freq.update(counts.keys())

    def idf(term):
        return math.log((n_docs + 1) / (doc_freq.get(term, 0) + 1)) + 1

    query_counts = Counter(query_tokens)
    query_vector = {t: c * idf(t) for t, c in query_counts.items()}
    query_norm = math.sqrt(sum(v * v for v in query_vector.values())) or 1

    scored = []
    for doc, counts in zip(corpus, doc_tokens):
        vector = {t: c * idf(t) for t, c in counts.items()}
        norm = math.sqrt(sum(v * v for v in vector.values())) or 1
        dot = sum(query_vector[t] * vector.get(t, 0) for t in query_vector)
        score = dot / (query_norm * norm)

        if score >= min_score:
            scored.append({**doc, 'score': round(score, 3)})

    scored.sort(key=lambda d: d['score'], reverse=True)
    return scored[:top_k]
