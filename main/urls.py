from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("dashboard/login/", auth_views.LoginView.as_view(template_name="main/login.html", redirect_authenticated_user=True), name="login"),
    path("dashboard/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("dashboard/participants/", views.participant_list, name="participants"),
    path("dashboard/participants/<str:public_id>/", views.participant_detail, name="participant_detail"),
    path("dashboard/follow-up/", views.followup, name="followup"),
    path("dashboard/reports/", views.report_index, name="reports"),
    path("dashboard/reports/<slug:slug>/", views.report_view, name="report_view"),
    path("dashboard/reports/<slug:slug>/download/<str:fmt>/", views.report_download, name="report_download"),
    path("dashboard/data-quality/", views.data_quality, name="data_quality"),
    path("dashboard/import/", views.import_data, name="import_data"),
    path("dashboard/recalculate/", views.recalculate, name="recalculate"),
    path("dashboard/methodology/", views.methodology, name="methodology"),
]
