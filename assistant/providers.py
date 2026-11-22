"""
AI provider layer (the ONLY place that talks to a language model).

How it works
------------
settings.AI_CHAIN lists models, best first, for example
    gemini:gemini-3-flash,groq:openai/gpt-oss-120b,gemini:gemini-2.5-flash

stream_chat() tries them in order and moves on to the next one when a model
  * has no API key,
  * has used up its safe daily allowance (AI_DAILY_CAP_*),
  * is cooling down after a rate-limit (HTTP 429),
  * rejects the request (bad key, wrong model name, provider error).
If all of them fail, a ProviderError is raised and the assistant falls back to
"facts only" answers. All keys here are FREE-tier keys: nothing can bill you.

To add another provider later: write one function like _raw_groq() and add it
to RAW_STREAMS. No other file has to change.
"""

import json
import urllib.error
import urllib.request
from collections import namedtuple
from datetime import timedelta

from django.conf import settings
from django.db.models import F
from django.utils import timezone

from .models import ModelUsage

Candidate = namedtuple('Candidate', 'provider model')
Candidate.key = property(lambda self: f'{self.provider}:{self.model}')


class ProviderError(Exception):
    """Something went wrong with the model. The message is safe to show to users."""


class ProviderUnavailable(ProviderError):
    """No model can be reached or none is configured."""


class ModelMissing(ProviderError):
    """The server runs, but the model name is not accepted."""


class RateLimited(ProviderError):
    """The model's free limit was reached."""


KNOWN_PROVIDERS = ('gemini', 'groq', 'ollama')


# ---------------------------------------------------------------- the chain
def parse_chain():
    entries = []
    for raw in (settings.AI_CHAIN or '').split(','):
        provider, _, model = raw.strip().partition(':')
        provider, model = provider.lower(), model.strip()
        if provider in KNOWN_PROVIDERS and model:
            entries.append(Candidate(provider, model))
    return entries


def _has_key(provider):
    if provider == 'gemini':
        return bool(settings.GEMINI_API_KEY)
    if provider == 'groq':
        return bool(settings.GROQ_API_KEY)
    return True   # ollama needs no key


def _daily_cap(provider):
    if provider == 'gemini':
        return settings.AI_DAILY_CAP_GEMINI
    if provider == 'groq':
        return settings.AI_DAILY_CAP_GROQ
    return None   # a local model has no daily limit


def provider_enabled():
    """True if at least one model in the chain could be used (it has a key)."""
    return any(_has_key(c.provider) for c in parse_chain())


# ------------------------------------------------------------------- usage
def _usage_row(key):
    row, _ = ModelUsage.objects.get_or_create(day=timezone.now().date(), model_key=key)
    return row


def availability(candidate):
    """Return (ok, reason). reason: ok | no_key | daily_cap | cooling | rejected."""
    if not _has_key(candidate.provider):
        return False, 'no_key'
    row = ModelUsage.objects.filter(day=timezone.now().date(), model_key=candidate.key).first()
    if row is None:
        return True, 'ok'
    if row.blocked_until and row.blocked_until > timezone.now():
        return False, 'rejected' if row.last_error.startswith('rejected') else 'cooling'
    cap = _daily_cap(candidate.provider)
    if cap is not None and row.requests >= cap:
        return False, 'daily_cap'
    return True, 'ok'


def _record_request(key):
    row = _usage_row(key)
    ModelUsage.objects.filter(pk=row.pk).update(requests=F('requests') + 1)


def _block(key, seconds, message):
    row = _usage_row(key)
    row.blocked_until = timezone.now() + timedelta(seconds=seconds)
    row.last_error = message[:200]
    row.save(update_fields=['blocked_until', 'last_error'])


def _seconds_until_utc_midnight():
    now = timezone.now()
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(60, int((tomorrow - now).total_seconds()))


# ------------------------------------------------------ hide private thinking
class ThinkFilter:
    """Some models print their private reasoning between <think> tags. Hide it."""

    OPEN, CLOSE = '<think>', '</think>'

    def __init__(self):
        self.buffer = ''
        self.inside = False

    def feed(self, text):
        self.buffer += text
        out = ''
        while True:
            if self.inside:
                end = self.buffer.find(self.CLOSE)
                if end == -1:
                    self.buffer = self.buffer[-len(self.CLOSE):]
                    return out
                self.buffer = self.buffer[end + len(self.CLOSE):].lstrip('\n')
                self.inside = False
            else:
                start = self.buffer.find(self.OPEN)
                if start == -1:
                    keep = len(self.OPEN) - 1          # might be the start of "<think>"
                    out += self.buffer[:-keep] if len(self.buffer) > keep else ''
                    self.buffer = self.buffer[-keep:] if len(self.buffer) > keep else self.buffer
                    return out
                out += self.buffer[:start]
                self.buffer = self.buffer[start + len(self.OPEN):]
                self.inside = True

    def flush(self):
        rest = '' if self.inside else self.buffer
        self.buffer = ''
        return rest


# ------------------------------------------------------------- HTTP openers
# Some services (Groq, for example) sit behind Cloudflare, which rejects Python's default
# "Python-urllib" User-Agent with HTTP 403 "error code: 1010". A normal name fixes that.
USER_AGENT = 'PathWise/1.0 (study-abroad planner; +https://github.com/piash1401/PathWise)'


def _open_http(url, payload, headers):
    """POST JSON and return the open response. Separate function so tests can replace it."""
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'User-Agent': USER_AGENT, 'Accept': '*/*', **headers},
        method='POST',
    )
    return urllib.request.urlopen(request, timeout=settings.AI_TIMEOUT)


def _open_stream(payload):
    """Open Ollama's streaming chat. Separate function so tests can replace it."""
    request = urllib.request.Request(
        f'{settings.OLLAMA_BASE_URL}/api/chat',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'}, method='POST',
    )
    return urllib.request.urlopen(request, timeout=settings.OLLAMA_TIMEOUT)


def _sse_events(response):
    """Yield the JSON objects of a Server-Sent-Events response ("data: {...}" lines)."""
    with response:
        for raw in response:
            line = raw.decode('utf-8', errors='ignore').strip() if isinstance(raw, bytes) else str(raw).strip()
            if not line.startswith('data:'):
                continue
            data = line[5:].strip()
            if data == '[DONE]':
                return
            try:
                yield json.loads(data)
            except ValueError:
                continue


# --------------------------------------------------- one raw stream per provider
def _raw_gemini(model, messages):
    system = '\n\n'.join(m['content'] for m in messages if m['role'] == 'system')
    contents = []
    for message in messages:
        if message['role'] == 'system':
            continue
        role = 'model' if message['role'] == 'assistant' else 'user'
        if contents and contents[-1]['role'] == role:                 # Gemini wants alternating roles
            contents[-1]['parts'][0]['text'] += '\n\n' + message['content']
        else:
            contents.append({'role': role, 'parts': [{'text': message['content']}]})

    payload = {
        'contents': contents,
        'generationConfig': {'temperature': 0.3, 'maxOutputTokens': settings.AI_MAX_OUTPUT_TOKENS},
    }
    if system:
        payload['systemInstruction'] = {'parts': [{'text': system}]}

    url = f'{settings.GEMINI_BASE_URL}/v1beta/models/{model}:streamGenerateContent?alt=sse'
    response = _open_http(url, payload, {'x-goog-api-key': settings.GEMINI_API_KEY})
    for event in _sse_events(response):
        block = (event.get('promptFeedback') or {}).get('blockReason')
        if block:
            raise ProviderError('The AI model declined to answer this message.')
        for candidate in event.get('candidates', []):
            for part in (candidate.get('content') or {}).get('parts', []):
                if part.get('thought'):
                    continue
                if part.get('text'):
                    yield part['text']


def _raw_groq(model, messages):
    payload = {
        'model': model, 'messages': messages, 'stream': True,
        'temperature': 0.3, 'max_tokens': settings.AI_MAX_OUTPUT_TOKENS,
    }
    url = f'{settings.GROQ_BASE_URL}/openai/v1/chat/completions'
    response = _open_http(url, payload, {'Authorization': f'Bearer {settings.GROQ_API_KEY}'})
    for event in _sse_events(response):
        for choice in event.get('choices', []):
            text = (choice.get('delta') or {}).get('content')
            if text:
                yield text


def _raw_ollama(model, messages):
    payload = {
        'model': model, 'messages': messages, 'stream': True, 'keep_alive': '30m',
        'options': {'temperature': 0.3, 'num_ctx': settings.OLLAMA_NUM_CTX},
    }
    response = _open_stream(payload)
    with response:
        for raw in response:
            line = raw.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if data.get('error'):
                if 'not found' in str(data['error']).lower():
                    raise ModelMissing(f'The model "{model}" is not installed. Run: ollama pull {model}')
                raise ProviderError('The local AI model stopped with an error.')
            piece = (data.get('message') or {}).get('content', '')
            if piece:
                yield piece
            if data.get('done'):
                break


RAW_STREAMS = {'gemini': _raw_gemini, 'groq': _raw_groq, 'ollama': _raw_ollama}


# ----------------------------------------------------------- error handling
def _http_error(candidate, error):
    """Turn an HTTP error into a ProviderError and remember when to retry."""
    body = ''
    try:
        body = error.read().decode('utf-8', errors='ignore').lower()
    except Exception:
        pass
    key, code = candidate.key, error.code

    if code == 429:
        daily = any(w in body for w in ('per day', 'perday', 'daily', 'tokens per day', 'tpd'))
        retry_after = None
        try:
            retry_after = int(float(error.headers.get('retry-after')))   # type: ignore[union-attr]
        except Exception:
            pass
        seconds = _seconds_until_utc_midnight() if daily else min(max(retry_after or 60, 30), 3600)
        _block(key, seconds, 'rate limited')
        return RateLimited('A free AI model hit its limit.')

    if code in (401, 403):
        _block(key, 300, 'rejected: the key was refused (check the key and that the service works in your country)')
        return ProviderError('An AI key was refused.')

    if code in (400, 404):
        _block(key, 300, 'rejected: model name not accepted (check AI_CHAIN)')
        if candidate.provider == 'ollama':
            return ModelMissing(f'The model "{candidate.model}" is not installed. '
                                f'Run: ollama pull {candidate.model}')
        return ModelMissing('An AI model name was not accepted.')

    _block(key, 60, f'error {code}')
    return ProviderError('An AI service returned an error.')


def _stream_one(candidate, messages):
    think = ThinkFilter()
    try:
        for piece in RAW_STREAMS[candidate.provider](candidate.model, messages):
            visible = think.feed(piece)
            if visible:
                yield visible
        tail = think.flush()
        if tail:
            yield tail
    except urllib.error.HTTPError as error:
        raise _http_error(candidate, error)
    except ProviderError:
        raise
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
        _block(candidate.key, 60, 'unreachable')
        raise ProviderUnavailable('An AI service could not be reached.')


# ------------------------------------------------------------------- public
def _outcome(error):
    if isinstance(error, RateLimited):
        return 'rate'
    if isinstance(error, ModelMissing) or 'refused' in str(error):
        return 'refused'
    if isinstance(error, ProviderUnavailable):
        return 'unreachable'
    return 'error'


def _failure_message(outcomes):
    """Say what really went wrong, not just "busy"."""
    if 'refused' in outcomes:
        return ('The AI models refused the request (wrong key or model name?). '
                'Run "python manage.py check_ai" to see why, or open "Model details" in the chat panel.')
    if outcomes & {'rate', 'limit'}:
        return 'All free AI models are busy or have reached their daily limit.'
    if 'unreachable' in outcomes:
        return 'The AI services could not be reached. Check your internet connection.'
    return 'The free AI models could not answer right now.'


def stream_chat(messages, info=None):
    """
    Yield text pieces from the first model that works. `info` (a dict) receives
    {"model": "provider:model"} once a model starts answering.
    Raises ProviderError (with the real reason) when nothing could answer.
    Only answers that really started count toward a model's daily allowance.
    """
    info = info if info is not None else {}
    chain = parse_chain()
    if not chain or not any(_has_key(c.provider) for c in chain):
        raise ProviderUnavailable('No AI model is configured yet (add a GEMINI_API_KEY or GROQ_API_KEY).')

    skipped = {'no_key': 'no_key', 'daily_cap': 'limit', 'cooling': 'rate', 'rejected': 'refused'}
    outcomes = set()

    for candidate in chain:
        ok, reason = availability(candidate)
        if not ok:
            outcomes.add(skipped.get(reason, 'error'))
            continue
        started = False
        try:
            for piece in _stream_one(candidate, messages):
                if not started:
                    started = True
                    info['model'] = candidate.key
                    _record_request(candidate.key)
                yield piece
            if started:
                return
            outcomes.add('empty')
        except ProviderError as error:
            if started:
                raise ProviderError('The AI service stopped while answering.')
            outcomes.add(_outcome(error))

    outcomes.discard('no_key')
    raise ProviderUnavailable(_failure_message(outcomes))


def _ollama_status(model):
    try:
        request = urllib.request.Request(f'{settings.OLLAMA_BASE_URL}/api/tags')
        with urllib.request.urlopen(request, timeout=3) as response:
            data = json.loads(response.read().decode('utf-8'))
        names = [m.get('name', '') for m in data.get('models', [])]
        installed = any(n == model or (':' not in model and n.split(':')[0] == model) for n in names)
        return {'reachable': True, 'model_installed': installed}
    except Exception:
        return {'reachable': False, 'model_installed': False}


def check_status():
    """What the interface shows: every model in the chain and whether it can be used now."""
    models = []
    for candidate in parse_chain():
        ok, reason = availability(candidate)
        row = ModelUsage.objects.filter(day=timezone.now().date(), model_key=candidate.key).first()
        entry = {
            'provider': candidate.provider, 'model': candidate.model, 'available': ok, 'reason': reason,
            'used_today': row.requests if row else 0, 'daily_cap': _daily_cap(candidate.provider),
            'last_error': row.last_error if row and not ok else '',
        }
        if candidate.provider == 'ollama':
            entry.update(_ollama_status(candidate.model))
            entry['available'] = ok and entry['reachable'] and entry['model_installed']
        models.append(entry)
    return {
        'enabled': provider_enabled(),
        'available': any(m['available'] for m in models),
        'models': models,
    }
