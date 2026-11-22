from django.urls import path

from .views import (
    AskView,
    ChatStreamView,
    ChatView,
    ConversationDetailView,
    ConversationListView,
    StatusView,
)

urlpatterns = [
    path('ask/', AskView.as_view()),
    path('chat/', ChatView.as_view()),
    path('chat/stream/', ChatStreamView.as_view()),
    path('conversations/', ConversationListView.as_view()),
    path('conversations/<int:pk>/', ConversationDetailView.as_view()),
    path('status/', StatusView.as_view()),
]
