# Python
import base64

# Libs
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from Crypto.Util.Padding import pad


def encryptor(plain_text) -> (tuple):
    """
    It is responsible for encrypting the data.
    """
    key = get_random_bytes(16)
    data = plain_text.encode()
    cipher = AES.new(key, AES.MODE_CBC)
    cipher_text = cipher.encrypt(pad(data, AES.block_size))
    iv = cipher.iv

    return (
        base64.b64encode(key).decode(),
        base64.b64encode(cipher_text).decode(),
        base64.b64encode(iv).decode()
    )


def decryptor(key, data, iv) -> (str):
    """
    It is responsible for decrypting the data.
    """
    key = base64.b64decode(key)
    data = base64.b64decode(data)
    iv = base64.b64decode(iv)

    decrypt_cipher = AES.new(key, AES.MODE_CBC, iv)
    plain_text = decrypt_cipher.decrypt(data)
    return plain_text.decode("utf-8")
