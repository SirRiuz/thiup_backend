# Python
from django.conf import settings

# Libs
import geoip2.database


def get_country(address) -> (str):
    """Get the country name of the user mask"""
    with geoip2.database.Reader(settings.GEOLITE_DIR) as reader:
        try:
            response = reader.city(address)
            return response.country.iso_code
        except geoip2.errors.AddressNotFoundError:
            return "Unknow"
