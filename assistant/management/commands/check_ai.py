"""
Test every AI model in AI_CHAIN with one tiny request and explain any problem.

    python manage.py check_ai            # test the models
    python manage.py check_ai --reset    # also clear today's counters and cooldowns first

Use this whenever the chat says the AI is unavailable. It prints the real reason
(wrong model name, refused key, rate limit, no internet). It never prints your keys.
"""

import json
import re
import urllib.error

from django.conf import settings
from django.core.management.base import BaseCommand

from assistant import providers
from assistant.models import ModelUsage

HINTS = {
    400: 'The request was not accepted. Usually the model name is wrong: check AI_CHAIN in .env.',
    401: 'The key was refused. Copy it again from the dashboard (no spaces or quotes).',
    403: 'The key was refused, or this service is not allowed from your account or country.',
    404: 'This model name does not exist. Fix it in AI_CHAIN (AI Studio: "Get code"; Groq: Limits page).',
    413: 'The request was too large for this model\'s free limit.',
    429: 'Rate limited or no free quota for this model right now. Try later or remove it from AI_CHAIN.',
}


def _message(body):
    try:
        data = json.loads(body)
        error = data.get('error', data)
        return str(error.get('message', error))[:300] if isinstance(error, dict) else str(error)[:300]
    except Exception:
        return body.strip()[:300]


class Command(BaseCommand):
    help = 'Test every model in AI_CHAIN with one tiny request.'

    def add_arguments(self, parser):
        parser.add_argument('--reset', action='store_true', help="Clear today's usage counters and cooldowns first.")

    def handle(self, *args, **options):
        if options['reset']:
            deleted = ModelUsage.objects.all().delete()[0]
            self.stdout.write(f'Cleared {deleted} usage record(s).')

        def key_line(name, value):
            return f'{name}: ' + (f'set ({len(value)} characters)' if value else 'NOT SET')

        self.stdout.write(key_line('GEMINI_API_KEY', settings.GEMINI_API_KEY))
        self.stdout.write(key_line('GROQ_API_KEY', settings.GROQ_API_KEY))

        chain = providers.parse_chain()
        if not chain:
            self.stdout.write(self.style.WARNING('AI_CHAIN is empty, so the AI is switched off.'))
            return

        working = 0
        for candidate in chain:
            label = f'{candidate.provider}:{candidate.model}'
            if not providers._has_key(candidate.provider):
                self.stdout.write(self.style.WARNING(f'SKIP   {label}  (no key for {candidate.provider})'))
                continue
            try:
                text = ''.join(providers.RAW_STREAMS[candidate.provider](
                    candidate.model, [{'role': 'user', 'content': 'Reply with the single word OK.'}]))
                working += 1
                self.stdout.write(self.style.SUCCESS(f'OK     {label}  -> "{text.strip()[:40]}"'))
            except urllib.error.HTTPError as error:
                body = ''
                try:
                    body = error.read().decode('utf-8', errors='ignore')
                except Exception:
                    pass
                self.stdout.write(self.style.ERROR(f'FAIL   {label}  HTTP {error.code}'))
                if 'error code: 1010' in body:
                    self.stdout.write('       Blocked by the service\'s firewall (Cloudflare 1010) because of how the request '
                                      'introduced itself, not because of your key. Use the newest PathWise files.')
                else:
                    self.stdout.write(f'       {HINTS.get(error.code, "The service returned an error.")}')
                if body:
                    self.stdout.write(f'       Service said: {_message(body)}')
                suggestion = re.search(r'use models/([A-Za-z0-9._-]+)', body)
                if suggestion:
                    self.stdout.write(self.style.WARNING(
                        f'       Suggested replacement: {candidate.provider}:{suggestion.group(1)}'))
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as error:
                self.stdout.write(self.style.ERROR(f'FAIL   {label}  cannot connect'))
                self.stdout.write(f'       Check your internet connection. ({error})')
            except providers.ProviderError as error:
                self.stdout.write(self.style.ERROR(f'FAIL   {label}  {error}'))

        self.stdout.write('')
        if working:
            self.stdout.write(self.style.SUCCESS(f'{working} of {len(chain)} model(s) work. The assistant can use them.'))
        else:
            self.stdout.write(self.style.ERROR('No model worked. Fix the problems above, then run this command again.'))
