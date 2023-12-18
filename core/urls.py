# Django
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from rest_framework_swagger.views import get_swagger_view
from rest_framework import permissions

# Libs
from drf_yasg.views import get_schema_view
from drf_yasg import openapi


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
    path("admin/", admin.site.urls),
    path("api/", include("apps.threads.urls")),
    path("api/", include("apps.reactions.urls")),
    path("api/", include("apps.tags.urls")),
    path("api/", include("apps.security.urls")),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
