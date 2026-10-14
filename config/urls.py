from django.urls import path

from tracking import views

urlpatterns = [
    path('v1/location-updates', views.LocationUpdateView.as_view()),
    path('v1/parent/children/overview', views.ParentChildrenOverviewView.as_view()),
    path('v1/webhooks/ai-tutor/sessions', views.TutorSessionWebhookView.as_view()),
]
