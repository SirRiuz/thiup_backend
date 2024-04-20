# Django
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination


class CustomThreadPagination(PageNumberPagination):

    def get_paginated_response(self, data) -> (Response):
        context = data.get("context", {})
        data = data["data"]
        response = super().get_paginated_response(data)
        response.data = {**context, **response.data}
        return response
