# Python
import json
import base64

# Django
from rest_framework.renderers import BaseRenderer

# Libs
from cryptography.fernet import Fernet


class MiRenderizador(BaseRenderer):
    
    # Backend response encode
    media_type = 'application/bre'
    format = 'mi_formato'
    charset = 'utf-8'

    def render(self, data, media_type=None, renderer_context=None) -> (str):
        request = renderer_context['request']
        response = renderer_context['response']

        # API_KEY + MASK_HASH

        print("Request -> ")
        print(request)
        print()
        print("response -> ")
        print(response)



        clave = Fernet.generate_key()
        cipher_suite = Fernet(clave)



        mi_string = cipher_suite.encrypt(str(data).encode()).decode()[::-1]
        bytes_hex = ' '.join([format(byte, '02x') for byte in mi_string.encode('utf-8')])

        return bytes_hex
        return cipher_suite.encrypt(str(data).encode()).decode()
