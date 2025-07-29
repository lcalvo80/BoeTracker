import re

def clean_code_block(text):
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())

def extract_section(text, label):
    pattern = rf"{label}[:：]?\s*(.*?)(?=\n[A-ZÁÉÍÓÚÑ][^:：\n]+[:：]|\Z)"
    match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else ""
