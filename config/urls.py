from django.urls import path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from tracking import views

urlpatterns = [
    path('v1/location-updates', views.LocationUpdateView.as_view()),
    path('v1/parent/children/overview', views.ParentChildrenOverviewView.as_view()),
    path('v1/webhooks/ai-tutor/sessions', views.TutorSessionWebhookView.as_view()),
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
]
