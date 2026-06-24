# Django
from django.contrib import admin
from django.urls import path, re_path, include
from django.conf import settings
from django.conf.urls.static import static
from rest_framework import permissions

# Libs
from drf_yasg.views import get_schema_view
from drf_yasg import openapi

# Views
from app.rest.health import HealthCheckView
from app.methods.storage_backends import (
    local_backend_active,
    local_upload_put,
    LOCAL_UPLOAD_ROUTE,
)


schema_view = get_schema_view(
    openapi.Info(
        title="Thriup Rest API",
        default_version='v1',
        description="Descripción de tu API",
        terms_of_service="https://www.tuapi.com/terms/",
        contact=openapi.Contact(email="contacto@tuapi.com"),
        license=openapi.License(name="Licencia de tu API"),
    ),
    public=True,
    permission_classes=(permissions.IsAdminUser,),
)

urlpatterns = [
    path('admin/swagger/', schema_view.with_ui('swagger', cache_timeout=0), name='schema-swagger-ui'),
    # Decoy /admin/ — django-honeypot logs scanners and serves a fake login.
    path("admin/", include("honeypot.urls")),
    # Real admin lives at INTERNAL_ADMIN_URL (set in .env).
    path(settings.INTERNAL_ADMIN_URL, admin.site.urls),
    # PRIVATE healthcheck + E2E (same rules as the other endpoints).
    path("health/", HealthCheckView.as_view()),
    # REST API (threads, reactions, tags, search, users/me).
    # GraphQL (future) will be mounted separately without touching this.
    path("", include("app.rest.urls")),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# Local-only PUT receiver: the local equivalent of R2's upload target, the
# destination of the presigned PUT when the LocalStorageBackend is active
# (DEBUG + no R2 + plain transport). NOT a business endpoint and NOT in the API
# schema — registered only in that mode so it never exists in production.
if local_backend_active():
    urlpatterns += [
        re_path(
            rf"^{LOCAL_UPLOAD_ROUTE}/(?P<key>.+)$", local_upload_put),
    ]
