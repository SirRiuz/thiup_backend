# Django
from django.core.paginator import Paginator
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class CustomThreadPagination(PageNumberPagination):
    def get_paginated_response(self, data) -> Response:
        context = data.get("context", {})
        data = data["data"]
        response = super().get_paginated_response(data)
        response.data = {**context, **response.data}
        return response


class PrecomputedCountPaginator(Paginator):
    """Django Paginator that reuses a COUNT the view already ran.

    `count` is a cached_property on the base class: seeding the instance
    __dict__ short-circuits it, so no second COUNT(*) is issued."""

    def __init__(self, object_list, per_page, precomputed_count=None, **kwargs):
        super().__init__(object_list, per_page, **kwargs)
        if precomputed_count is not None:
            self.__dict__["count"] = precomputed_count


class SearchPagination(CustomThreadPagination):
    """
    Pagination for the search endpoint: the view ALREADY counts the active
    tab for the `counts` dict (tab badges), so the page math reuses that
    number instead of issuing a second COUNT — measured, that duplicate
    COUNT over the annotated posts queryset (Subquery + JOIN included) was
    one of the most expensive queries of the whole request (~23 ms).
    """

    def paginate_queryset(self, queryset, request, view=None):
        precomputed = getattr(view, "precomputed_count", None)
        if precomputed is not None:
            self.django_paginator_class = lambda object_list, per_page: PrecomputedCountPaginator(
                object_list, per_page, precomputed_count=precomputed
            )
        return super().paginate_queryset(queryset, request, view)
