#app/utils/compression.py
import gzip
import base64

def compress_json(text: str) -> str:
    return base64.b64encode(gzip.compress(text.encode("utf-8"))).decode("utf-8")

def decompress_json(b64_text: str) -> str:
    return gzip.decompress(base64.b64decode(b64_text)).decode("utf-8")