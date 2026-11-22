import json

from django.conf import settings
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import providers, search
from .models import Conversation
from .orchestrator import MAX_INPUT_CHARS, run_chat, student_usage


def _conversation_json(conversation):
    return {
        'id': conversation.id,
        'title': conversation.title,
        'updated_at': conversation.updated_at,
    }


def _message_json(message):
    return {
        'id': message.id,
        'role': message.role,
        'content': message.content,
        'sources': message.sources,
        'meta': message.meta,
        'created_at': message.created_at,
    }


class AssistantAPIView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'ai'

    def validate_message(self, request):
        message = request.data.get('message', '')
        if not isinstance(message, str) or not message.strip():
            return None, Response({'error': 'Write a message first.'}, status=status.HTTP_400_BAD_REQUEST)
        if len(message) > MAX_INPUT_CHARS:
            return None, Response(
                {'error': f'Messages can have at most {MAX_INPUT_CHARS} characters.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return message, None

    def get_conversation(self, request):
        """Existing conversation (only the student's own) or a new one."""
        conversation_id = request.data.get('conversation_id')
        if conversation_id in (None, ''):
            if Conversation.objects.filter(user=request.user).count() >= settings.AI_MAX_CONVERSATIONS_PER_USER:
                return None, Response(
                    {'error': 'You have reached the limit of saved conversations. Delete an old one first.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return Conversation.objects.create(user=request.user), None

        try:
            conversation = Conversation.objects.get(id=int(conversation_id), user=request.user)
        except (ValueError, TypeError, Conversation.DoesNotExist):
            return None, Response({'error': 'Conversation not found.'}, status=status.HTTP_404_NOT_FOUND)

        if conversation.messages.count() >= settings.AI_MAX_MESSAGES_PER_CONVERSATION:
            return None, Response(
                {'error': 'This conversation is full. Please start a new chat.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return conversation, None


class ChatView(AssistantAPIView):
    """POST {"message", "conversation_id"?} -> complete answer (no streaming)."""

    def post(self, request):
        message, error = self.validate_message(request)
        if error:
            return error
        conversation, error = self.get_conversation(request)
        if error:
            return error

        meta, done = {}, {}
        for event in run_chat(request.user, conversation, message):
            if event['type'] == 'meta':
                meta = event
            elif event['type'] == 'done':
                done = event

        return Response({
            'conversation': _conversation_json(conversation),
            'answer': done['text'],
            'message_id': done['message_id'],
            'mode': done['mode'],
            'uncited': done['uncited'],
            'sources': meta.get('sources', []),
            'grounding': meta.get('grounding', {}),
            'notes': meta.get('notes', []),
        })


class ChatStreamView(AssistantAPIView):
    """Same as ChatView, but answers arrive piece by piece as newline-separated JSON events."""

    def post(self, request):
        message, error = self.validate_message(request)
        if error:
            return error
        conversation, error = self.get_conversation(request)
        if error:
            return error

        def events():
            try:
                yield json.dumps({'type': 'start', 'conversation': _conversation_json(conversation)}, default=str) + '\n'
                for event in run_chat(request.user, conversation, message):
                    yield json.dumps(event, default=str) + '\n'
            except Exception:
                yield json.dumps({'type': 'error', 'text': 'Something went wrong. Please try again.'}) + '\n'

        response = StreamingHttpResponse(events(), content_type='application/x-ndjson')
        response['Cache-Control'] = 'no-cache'
        response['X-Accel-Buffering'] = 'no'
        return response


class ConversationListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        conversations = Conversation.objects.filter(user=request.user)
        return Response([_conversation_json(c) for c in conversations])


class ConversationDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _get(self, request, pk):
        return get_object_or_404(Conversation, pk=pk, user=request.user)

    def get(self, request, pk):
        conversation = self._get(request, pk)
        return Response({
            'conversation': _conversation_json(conversation),
            'messages': [_message_json(m) for m in conversation.messages.all()],
        })

    def patch(self, request, pk):
        conversation = self._get(request, pk)
        title = request.data.get('title', '')
        if not isinstance(title, str) or not title.strip():
            return Response({'error': 'Enter a title.'}, status=status.HTTP_400_BAD_REQUEST)
        conversation.title = title.strip()[:120]
        conversation.save(update_fields=['title', 'updated_at'])
        return Response(_conversation_json(conversation))

    def delete(self, request, pk):
        self._get(request, pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class StatusView(APIView):
    """Tells the interface (and you, while debugging) what is switched on."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        used, limit = student_usage(request.user)
        return Response({
            'ai': providers.check_status(),
            'web_search': search.status(),
            'student': {'used': used, 'limit': limit},
        })


class AskView(AssistantAPIView):
    """
    Older single-question endpoint, kept so nothing breaks.
    POST {"question"} -> {"answer", "sources", "mode"}. Nothing is saved.
    """

    def post(self, request):
        question = request.data.get('question', '')
        if not isinstance(question, str) or not 3 <= len(question.strip()) <= 1000:
            return Response(
                {'error': 'Write a question between 3 and 1000 characters.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        meta, done = {}, {}
        for event in run_chat(request.user, None, question, persist=False):
            if event['type'] == 'meta':
                meta = event
            elif event['type'] == 'done':
                done = event

        return Response({
            'answer': done['text'],
            'mode': 'llm' if done['mode'] == 'ai' else 'retrieval',
            'sources': meta.get('sources', []),
        })
