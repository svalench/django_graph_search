from django.urls import path

from .views import (
    ConversationalSearchAPIView,
    SearchAPIView,
    SimilarAPIView,
    StreamingSearchAPIView,
)


urlpatterns = [
    path("", SearchAPIView.as_view(), name="graph_search"),
    path("similar/<str:model>/<str:pk>/", SimilarAPIView.as_view(), name="graph_search_similar"),
    path(
        "conversation/",
        ConversationalSearchAPIView.as_view(),
        name="graph_search_conversation",
    ),
    path(
        "stream/",
        StreamingSearchAPIView.as_view(),
        name="graph_search_stream",
    ),
]

