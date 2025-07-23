import requests
import xml.etree.ElementTree as ET
from datetime import datetime
import logging

headers = {"Accept": "application/xml"}

def fetch_boe_xml():
    date_str = datetime.now().strftime('%Y%m%d')
    url = f"https://boe.es/datosabiertos/api/boe/sumario/20250714"
    #url=fhttps://boe.es/datosabiertos/api/boe/sumario/date_str"
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return ET.fromstring(response.text)
    except Exception as e:
        logging.error(f"❌ Error al obtener el BOE: {e}")
        return None