# Python
import os
from hashlib import sha256


def get_file_list(path: str) -> (list):
    """Returns a list with the items in a folder"""
    file_data = []
    for file in os.listdir(path):
        name  = file.split(".")[0] if "." in file else file
        extension = extension = file.split(".")[1] if "." in file else None
        file_data.append({
            "name": name,
            "extension": extension,
            "hash": sha256(file.encode()).hexdigest(),
            "path": f"{path}/{file}"
        })

    return file_data
