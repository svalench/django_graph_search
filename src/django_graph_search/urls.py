from django.urls import path

from .views import SearchAPIView, SimilarAPIView


urlpatterns = [
    path("", SearchAPIView.as_view(), name="graph_search"),
    path("similar/<str:model>/<str:pk>/", SimilarAPIView.as_view(), name="graph_search_similar"),
]

