from django.conf import settings
from django.db import models


class Conversation(models.Model):
    """One chat thread. Always belongs to exactly one student."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='ai_conversations',
    )
    title = models.CharField(max_length=120, default='New chat')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return f'{self.user} - {self.title}'


class Message(models.Model):
    ROLE_CHOICES = [('user', 'Student'), ('assistant', 'Assistant')]

    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name='messages'
    )
    role = models.CharField(max_length=10, choices=ROLE_CHOICES)
    content = models.TextField()
    sources = models.JSONField(default=list, blank=True)   # numbered sources shown under the answer
    meta = models.JSONField(default=dict, blank=True)      # mode, grounding, notes
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'id']


class SearchLog(models.Model):
    """
    Every real Tavily call is stored here. This gives us two things for free:
    a cache (same question within 24 h costs no credit) and a monthly counter
    that stops us before the free limit is reached.
    """

    query_hash = models.CharField(max_length=64, db_index=True)
    query = models.CharField(max_length=300)
    results = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class ModelUsage(models.Model):
    """
    Counts how many requests each AI model got today and remembers when a model
    is cooling down (rate limit hit) or was rejected. This protects the free
    daily allowance and lets the app skip a model that cannot answer.
    """

    day = models.DateField()
    model_key = models.CharField(max_length=120)          # e.g. "gemini:gemini-3-flash"
    requests = models.PositiveIntegerField(default=0)
    blocked_until = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=200, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['day', 'model_key'], name='unique_model_usage_per_day')
        ]
