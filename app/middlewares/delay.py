import time

class SimulateDelayMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Before passing the request to the next step, we wait 5 seconds
        time.sleep(5)
        response = self.get_response(request)
        return response
