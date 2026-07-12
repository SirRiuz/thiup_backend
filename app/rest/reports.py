# Django
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.status import HTTP_200_OK, HTTP_201_CREATED
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.viewsets import GenericViewSet

# Models
from app.models.report import Report
from app.models.thread import Thread
from app.permissions.captcha import human_validator

# Libs
from app.permissions.client import IsClientAuthenticated
from app.rest.serializers.report_serializer import ReportSerializer


class ReportsViewSet(GenericViewSet):
    """Create/update an anonymous report of a thread.

    Upsert keyed by (thread, reporter): if the same pseudonymous user reports
    the same thread again, the existing report is UPDATED (new category/reason),
    never duplicated. The reporter is the request's mask — derived server-side,
    never sent by the client (anonymity).
    """

    queryset = Report.objects.filter(is_active=True)
    serializer_class = ReportSerializer
    permission_classes = (IsClientAuthenticated,)
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = "reports"

    @human_validator
    def create(self, request) -> Response:
        """
        Create or update (upsert) a report for a thread.
        ---
        Request Body:

                {
                    "thread_id": "<public uid>",
                    "category": "spam_or_deception | harassment | hate_speech "
                                "| dangerous_or_self_harm | minors | other",
                    "reason": "optional, <= 300 chars"
                }

        Response codes:

            201 - Report created.
            200 - Existing report updated (re-report).
            400 - Invalid category / reason too long / missing thread_id.
            401 - The client is not authorized.
            404 - Thread not found.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # Resolve the thread by its PUBLIC uid (FKs are by UUID pk internally).
        thread = get_object_or_404(Thread, uid=data["thread_id"], is_active=True)
        category = data["category"]

        # Upsert: one report per (thread, reporter). The reporter is the
        # request's pseudonymous mask — never sent by the client.
        _, created = Report.objects.update_or_create(
            thread=thread,
            reporter=request.mask,
            defaults={
                "category": category,
                "reason": data["reason"],
                "is_priority": category == Report.MINORS,
            },
        )

        # Minimal response — never echo the report content back.
        return Response({"status": "ok"}, status=HTTP_201_CREATED if created else HTTP_200_OK)
