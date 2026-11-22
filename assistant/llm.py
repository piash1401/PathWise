"""
Answer writer for the AI counsellor.

If ANTHROPIC_API_KEY is set, a Claude model writes a short answer that may only
use the retrieved sources. If not, or if the call fails, we fall back to
showing the most relevant source excerpts. The app always works either way.
"""

import json
import re
import urllib.error
import urllib.request

from django.conf import settings

API_URL = 'https://api.anthropic.com/v1/messages'

SYSTEM_PROMPT = (
    "You are the PathWise counsellor for Bangladeshi students planning a master's "
    "degree abroad. Answer ONLY with facts found in the numbered sources provided. "
    "Cite sources like [1] or [2] after each fact. If the sources do not contain the "
    "answer, say so plainly and tell the student which official website to check. "
    "Mark any source whose status is 'pending' as not yet verified. Never promise "
    "admission, a scholarship or a visa. Amounts in sources are in the local currency "
    "unless stated otherwise. Keep the answer under 200 words, in simple English."
)


def llm_enabled():
    return bool(getattr(settings, 'ANTHROPIC_API_KEY', ''))


def _post(payload):
    """Send the request. Kept separate so tests can replace it."""
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'content-type': 'application/json',
            'x-api-key': settings.ANTHROPIC_API_KEY,
            'anthropic-version': '2023-06-01',
        },
        method='POST',
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode('utf-8'))


def _profile_summary(profile):
    if profile is None:
        return 'No profile saved yet.'
    return (
        f'Degree: {profile.highest_degree}; field: {profile.field_of_study}; '
        f'CGPA: {profile.grade}/{profile.grade_scale}; English: '
        f'{profile.english_test or "not taken"} {profile.overall_score or ""}; '
        f'annual budget: {profile.annual_budget_bdt} BDT; '
        f'preferred destinations: {", ".join(profile.destination_preferences) or "none"}.'
    )


def generate_answer(question, sources, profile=None):
    """Return (answer_text, mode). mode is "llm" or "retrieval"."""
    if llm_enabled():
        numbered = '\n\n'.join(
            f'[{i}] ({s["kind"]}, status: {s["status"]}) {s["title"]}\n{s["text"][:1500]}'
            for i, s in enumerate(sources, start=1)
        )
        payload = {
            'model': settings.ANTHROPIC_MODEL,
            'max_tokens': 700,
            'system': SYSTEM_PROMPT,
            'messages': [{
                'role': 'user',
                'content': (
                    f'<student_profile>{_profile_summary(profile)}</student_profile>\n'
                    f'<sources>\n{numbered}\n</sources>\n'
                    f'<question>{question}</question>'
                ),
            }],
        }
        try:
            data = _post(payload)
            text = ''.join(
                block.get('text', '') for block in data.get('content', [])
                if block.get('type') == 'text'
            ).strip()
            if text:
                return text, 'llm'
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError):
            pass  # fall through to the retrieval-only answer

    return extractive_answer(sources), 'retrieval'


def extractive_answer(sources):
    lines = ['These are the most relevant sources in our database:']
    for i, source in enumerate(sources, start=1):
        sentences = re.split(r'(?<=[.!?])\s+', source['text'])
        excerpt = ' '.join(sentences[:3])[:420]
        note = '' if source['status'] == 'verified' else ' (not yet verified)'
        lines.append(f'[{i}] {source["title"]}{note}: {excerpt}')
    lines.append('Please confirm important details on the official websites linked below.')
    return '\n\n'.join(lines)
