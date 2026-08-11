# Django
from django.urls import include, path, re_path
from rest_framework import routers

from app.rest.batch import BatchView
from app.rest.captcha import CaptchaVerifyView
from app.rest.config import ConfigView
from app.rest.gateway import GatewayView
from app.rest.masks import CurrentMaskView, MasksViewSet
from app.rest.notifications import NotificationsViewSet
from app.rest.reactions import ReactionsViewSet
from app.rest.reports import ReportsViewSet
from app.rest.search import SearchViewSet
from app.rest.tags import TagsViewSet
from app.rest.thread_files import ThreadFilesViewSet

# Views
from app.rest.threads import ThreadsViewSet
from app.rest.ticket import TicketView

router = routers.DefaultRouter()
router.register(r"threads", ThreadsViewSet)
router.register(r"reactions", ReactionsViewSet)
router.register(r"reports", ReportsViewSet, basename="reports")
router.register(r"thread-files", ThreadFilesViewSet, basename="thread-files")
router.register(r"tags", TagsViewSet)
router.register(r"search", SearchViewSet, basename="search")
router.register(r"users", MasksViewSet, basename="users")
router.register(r"notifications", NotificationsViewSet, basename="notifications")

urlpatterns = [
    # Client-assertion issuer (bootstrap of SINGLE_REQUEST_PROTECT):
    # the backend signs the ticket with its secret; the client only carries it.
    path("ticket/", TicketView.as_view()),
    # Flags de transporte (contrato fijo: cifrado + single-request siempre).
    path("config/", ConfigView.as_view()),
    # Current user (private + E2E): identity of one's own mask.
    path("me/", CurrentMaskView.as_view()),
    # Human-pass issuer: exchanges a single-use Cap captcha token for the
    # short-lived pass that entity-creating writes require (@human_validator).
    path("captcha/verify/", CaptchaVerifyView.as_view()),
    # Batch engagement ingestion (foundation of the future analytics
    # system): fire-and-forget, client-batched telemetry — no captcha, see
    # app/rest/batch.py.
    path("batch/", BatchView.as_view()),
    path("", include(router.urls)),
    # ── GATEWAY de transporte con PATH ÚNICO POR REQUEST /{token}/ ───────
    # COMODÍN (verificar, no registrar): los paths únicos no se pueden
    # registrar (infinitos). Va AL FINAL: las rutas reales (palabras:
    # ticket/config/threads/...) y el admin (con guion) se resuelven primero;
    # solo un segmento de 24 hex cae aquí. La vista RECOMPUTA el token a
    # partir del nonce del sobre y lo compara en tiempo constante (no casa →
    # 404). Con TODO el tráfico aquí cuando el cifrado está activo —ticket y
    # config incluidos—, las rutas reales siguen registradas para el
    # DISPATCH INTERNO y para el modo flag-off.
    re_path(r"^(?P<gw_hash>[0-9a-f]{24})/$", GatewayView.as_view()),
]
