from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("", views.landing, name="landing"),
    path("track/", views.track, name="track"),

    # operator app
    path("app/", views.dashboard, name="dashboard"),
    path("app/new/", views.new_shipment, name="new_shipment"),
    path("app/parse-address/", views.parse_address_ajax, name="parse_address"),
    path("app/paste/", views.paste, name="paste"),
    path("app/<int:pk>/print/", views.print_label, name="print_label"),
    path("app/<int:pk>/preview/", views.preview_draft, name="preview_draft"),
    # batch / AWB flow
    path("app/dispatch/", views.dispatch, name="dispatch"),
    path("app/add-to-batch/", views.add_to_batch, name="add_to_batch"),
    path("app/remove-from-batch/", views.remove_from_batch, name="remove_from_batch"),
    path("app/combine/", views.combine, name="combine"),
    path("app/finalize/", views.finalize, name="finalize"),
    path("app/print-all/", views.print_all, name="print_all"),
    path("app/print-zpl/", views.print_zpl, name="print_zpl"),
    path("app/paperwork/", views.print_paperwork, name="print_paperwork"),
    path("app/cancel/", views.cancel, name="cancel"),
    path("app/delete-all-labels/", views.delete_all_labels, name="delete_all_labels"),

    # auth
    path("login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
]
