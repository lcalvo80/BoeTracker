import requests
import xml.etree.ElementTree as ET
from datetime import datetime
import logging

headers = {"Accept": "application/xml"}

def fetch_boe_xml(date_obj=None):
    if date_obj is None:
        date_obj = datetime.now()
    date_str = date_obj.strftime('%Y%m%d')
    #url = f"https://boe.es/datosabiertos/api/boe/sumario/{date_str}"
    url = f"https://boe.es/datosabiertos/api/boe/sumario/20250909"

    logging.info(f"🌐 Fetching BOE for {date_str} → {url}")

    try:
        response = requests.get(url, headers=headers, timeout=30)

        if response.status_code == 404:
            logging.warning(f"📭 No BOE sumario available for {date_str}")
            return None

        response.raise_for_status()
        return ET.fromstring(response.content)

    except requests.exceptions.RequestException as e:
        logging.error(f"❌ HTTP error while fetching BOE: {e}")
    except ET.ParseError as e:
        logging.error(f"❌ XML parsing error: {e}")
    except Exception as e:
        logging.error(f"❌ Unexpected error: {e}")
    
    return None
