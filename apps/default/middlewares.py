import time

class SimulateDelayMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Antes de pasar la solicitud al siguiente paso, esperamos 5 segundos
        time.sleep(2)
        response = self.get_response(request)
        return response
