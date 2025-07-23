import os
import json
from utils.helpers import extract_section, clean_code_block

def get_openai_responses(title, content):
    import openai

    client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    try:
        # -------------------------
        # TÍTULO RESUMIDO
        # -------------------------
        title_prompt = (
            f"Resume este título oficial en un máximo de 10 palabras, usando lenguaje claro y directo. "
            f"Evita frases largas o lenguaje técnico. El resultado debe ser adecuado como título corto de una web informativa: {title}"
        )

        title_response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Resumes títulos del BOE de forma clara y accesible para el público general."},
                {"role": "user", "content": title_prompt}
            ],
            max_tokens=50,
            temperature=0.3
        )
        titulo_resumen = title_response.choices[0].message.content.strip()
        titulo_resumen = titulo_resumen.rstrip(".").strip()

        # -------------------------
        # RESUMEN ESTRUCTURADO
        # -------------------------
        resumen_prompt = f"""
Actúa como un experto asistente legal especializado en analizar publicaciones oficiales como el Boletín Oficial del Estado (BOE).

Tu tarea es leer el contenido proporcionado y generar un resumen estructurado, escrito en texto plano, con lenguaje claro, formal y neutral. El resultado debe seguir exactamente los siguientes encabezados, en el mismo orden, uno por línea, sin negritas, sin viñetas, sin guiones, sin emojis ni otros símbolos.

El resumen debe ser fiel al contenido, sin inventar información no presente en el texto original. Utiliza frases cortas, precisas y sin adornos. Sé directo, evita repeticiones o explicaciones sobre tu rol. No incluyas introducciones ni conclusiones adicionales fuera del formato requerido.

Encabezados requeridos:
Contexto:
Breve descripción del contexto legal o administrativo. Debe ocupar un solo párrafo.

Cambios clave:
Enumera los cambios más relevantes, uno por línea. No uses guiones ni símbolos.

Fechas clave:
Enumera fechas importantes con una breve descripción, una por línea. Ejemplo: 1 de enero de 2025: Entrada en vigor.

Conclusión:
Resumen final de implicaciones o próximos pasos relevantes. Debe ocupar un solo párrafo.

Contenido:
{content}
"""

        resumen_response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Eres un asistente legal."},
                {"role": "user", "content": resumen_prompt}
            ]
        )
        resumen_text = clean_code_block(resumen_response.choices[0].message.content.strip())

        resumen_json = {
            "context": extract_section(resumen_text, "Contexto"),
            "key_changes": [
                line.strip() for line in extract_section(resumen_text, "Cambios clave").split("\n") if line.strip()
            ],
            "key_dates_events": [
                line.strip() for line in extract_section(resumen_text, "Fechas clave").split("\n") if line.strip()
            ],
            "conclusion": extract_section(resumen_text, "Conclusión")
        }

        # -------------------------
        # IMPACTO LEGISLATIVO
        # -------------------------
        impacto_prompt = f"""
Actúa como un analista legislativo con experiencia en la evaluación de normativas oficiales publicadas en el Boletín Oficial del Estado (BOE).

Tu tarea es analizar el contenido proporcionado y generar una evaluación del impacto en texto plano, usando exclusivamente los encabezados indicados, en el orden especificado. Cada ítem debe ir en una línea separada. No utilices guiones, viñetas, comillas, ni markdown. Sé concreto, preciso y objetivo. No incluyas introducciones ni explicaciones adicionales fuera del formato.

Encabezados requeridos:
Afectados:
¿Quiénes se ven impactados por esta normativa? Enuméralos, uno por línea.

Cambios operativos:
¿Qué cambios concretos introduce? Enuméralos, uno por línea.

Riesgos potenciales:
Riesgos o desafíos. Enuméralos, uno por línea.

Beneficios previstos:
Beneficios esperados. Enuméralos, uno por línea.

Recomendaciones:
Sugerencias para los afectados o entidades implicadas. Enuméralas, una por línea.

Contenido:
{content}
"""

        impacto_response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Eres un analista legislativo."},
                {"role": "user", "content": impacto_prompt}
            ]
        )

        impacto_text = clean_code_block(impacto_response.choices[0].message.content.strip())

        impacto_json = {
            "afectados": [
                line.strip() for line in extract_section(impacto_text, "Afectados").split("\n") if line.strip()
            ],
            "cambios_operativos": [
                line.strip() for line in extract_section(impacto_text, "Cambios operativos").split("\n") if line.strip()
            ],
            "riesgos_potenciales": [
                line.strip() for line in extract_section(impacto_text, "Riesgos potenciales").split("\n") if line.strip()
            ],
            "beneficios_previstos": [
                line.strip() for line in extract_section(impacto_text, "Beneficios previstos").split("\n") if line.strip()
            ],
            "recomendaciones": [
                line.strip() for line in extract_section(impacto_text, "Recomendaciones").split("\n") if line.strip()
            ]
        }

        return titulo_resumen, json.dumps(resumen_json, ensure_ascii=False), json.dumps(impacto_json, ensure_ascii=False)

    except Exception as e:
        print(f"❌ Error con OpenAI: {e}")
        return "", json.dumps({}), json.dumps({})
