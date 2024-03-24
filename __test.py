
import base64
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from Crypto.Util.Padding import pad
import json



def encriptor(plain_text) -> (tuple):
    key = get_random_bytes(16)
    data = plain_text.encode()
    cipher = AES.new(key, AES.MODE_CBC)
    cipher_text = cipher.encrypt(pad(data, AES.block_size))
    iv = cipher.iv

    print("Key ->", base64.b64encode(key).decode())
    print("Data ->", base64.b64encode(cipher_text).decode())
    print("IV ->", base64.b64encode(iv).decode())

    return (
        base64.b64encode(key).decode(),
        base64.b64encode(cipher_text).decode(),
        base64.b64encode(iv).decode()
    )


def decriptor(key, data, iv):
    key = base64.b64decode(key)
    data = base64.b64decode(data)
    iv = base64.b64decode(iv)

    decrypt_cipher = AES.new(key, AES.MODE_CBC, iv)
    plain_text = decrypt_cipher.decrypt(data)
    return plain_text.decode("utf-8")


key, data, iv = encriptor(json.dumps(
    {
        "name": "Hello world",
        "results": [

        ]
    },
    indent=2
))


#result = decriptor("1ybIMMFEBdmFNXg1XJ4V1g==", "Q4TjHAAwWlrmO/cGNuawLQ==", "1ybIMMFEBdmFNXg1XJ4V1g==")
#print(result)

