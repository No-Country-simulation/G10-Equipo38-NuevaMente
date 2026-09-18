"""Casos factuales versionados para los tests de fidelidad (issue #10, §12.2).

Para qué sirven: §12.2 exige que los fixtures incluyan "negaciones,
unidades, números inventados, contradicciones, ausencia de contexto y
fallos de interpretación visual". Estos casos son el MATERIAL de prueba
del verificador de fidelidad (issue #24): cada uno trae un texto
"generado", la "evidencia" (fragmento de fuente) y el resultado ESPERADO
del juicio NLI — respaldada o no, con la razón.

Cómo se usa: los tests recorren CASOS_FACTUALES y verifican que la lógica
de fidelidad (cuando exista) coincida con `esperado.respaldada`. Hoy, un
test de estructura garantiza que los casos estén completos y categorizados.

Cómo agregar un caso: copiar el patrón, elegir la categoría correcta y
escribir la razón en términos verificables ("la fuente dice X, el texto
afirma NO-X"). No editar casos existentes sin anotarlo: son datos de
prueba versionados y cambiarlos en silencio invalida comparaciones.
"""

from __future__ import annotations

# Categorías exigidas por §12.2 (cerradas a propósito).
CATEGORIAS_FACTUALES = (
    "negacion",  # el texto invierte el sentido de la fuente
    "unidades",  # misma cifra, unidad distinta (ms vs s, MB vs Mb)
    "numero_inventado",  # cifra que la fuente no contiene
    "contradiccion",  # la fuente se contradice y hay que reportarlo
    "contexto_ausente",  # afirmación verdadera en general pero no respaldada por ESTA fuente
    "interpretacion_visual",  # afirmación sobre un diagrama no sustentada por su descripción
)

CASOS_FACTUALES: list[dict] = [
    {
        "id": "neg-001",
        "categoria": "negacion",
        "texto_generado": "El NAT Gateway NO permite tráfico entrante iniciado desde Internet.",
        "evidencia": "El NAT Gateway habilita la salida a Internet de instancias sin IP pública; ninguna conexión puede iniciarse hacia ellas.",
        "esperado": {"respaldada": True, "razon": "La fuente lo afirma explícitamente."},
    },
    {
        "id": "neg-002",
        "categoria": "negacion",
        "texto_generado": "Las reglas de Security List no mantienen estado.",
        "evidencia": "Las reglas de Security List son stateful: la respuesta de una conexión aceptada se permite automáticamente.",
        "esperado": {"respaldada": False, "razon": "La fuente afirma lo contrario del texto."},
    },
    {
        "id": "uni-001",
        "categoria": "unidades",
        "texto_generado": "El deadline de generación es de 5 minutos.",
        "evidencia": "Deadline de generación: 300 segundos desde ejecución, sin contar espera.",
        "esperado": {"respaldada": True, "razon": "300 segundos = 5 minutos."},
    },
    {
        "id": "uni-002",
        "categoria": "unidades",
        "texto_generado": "La cola admite hasta 5 trabajos por minuto.",
        "evidencia": "Cola de espera: hasta cinco trabajos; posición visible y cancelación.",
        "esperado": {
            "respaldada": False,
            "razon": "La fuente habla de 5 trabajos EN ESPERA, no de una tasa por minuto.",
        },
    },
    {
        "id": "num-001",
        "categoria": "numero_inventado",
        "texto_generado": "El paquete educativo puede pesar hasta 50 MB.",
        "evidencia": "El canónico se guarda como JSON aprobado; el documento no menciona tamaño de paquete.",
        "esperado": {"respaldada": False, "razon": "Cifra ausente de la fuente (número inventado)."},
    },
    {
        "id": "num-002",
        "categoria": "numero_inventado",
        "texto_generado": "Las sesiones duran 24 horas.",
        "evidencia": "Las sesiones usan tokens aleatorios y duran como máximo 24 horas.",
        "esperado": {"respaldada": True, "razon": "La cifra está en la fuente."},
    },
    {
        "id": "con-001",
        "categoria": "contradiccion",
        "texto_generado": "El borrado del espacio es inmediato y físico.",
        "evidencia": "Borrar el espacio revoca el acceso de inmediato y AGENDA la limpieza física de sus objetos.",
        "esperado": {"respaldada": False, "razon": "La revocación es inmediata; la limpieza física NO (es agendada)."},
    },
    {
        "id": "con-002",
        "categoria": "contradiccion",
        "texto_generado": "El mock de almacenamiento se activa automáticamente ante una falla de OCI.",
        "evidencia": "Con MOCK_OCI=0, un error de OCI devuelve un fallo visible. No hay cambio automático al mock.",
        "esperado": {"respaldada": False, "razon": "Contradice la política de la fuente."},
    },
    {
        "id": "ctx-001",
        "categoria": "contexto_ausente",
        "texto_generado": "Python 3.11 es la versión recomendada para el proyecto.",
        "evidencia": "GitHub Actions instala las dependencias fijadas de backend y frontend con Python 3.11.",
        "esperado": {"respaldada": True, "razon": "La fuente usa Python 3.11 para backend, frontend y CI."},
    },
    {
        "id": "ctx-002",
        "categoria": "contexto_ausente",
        "texto_generado": "Kubernetes orquesta los contenedores del proyecto.",
        "evidencia": "Se mantienen dos contenedores de aplicación y un proxy HTTPS, orquestados con Compose.",
        "esperado": {"respaldada": False, "razon": "La fuente menciona Compose, no Kubernetes."},
    },
    {
        "id": "vis-001",
        "categoria": "interpretacion_visual",
        "texto_generado": "En el diagrama, la base de datos está en la subred pública.",
        "evidencia": "Descripción del diagrama: la base de datos (puerto 1521) vive en la subred privada 10.0.2.0/24, sin IP pública.",
        "esperado": {"respaldada": False, "razon": "El diagrama la ubica en la subred privada."},
    },
    {
        "id": "vis-002",
        "categoria": "interpretacion_visual",
        "texto_generado": "El diagrama muestra un codo punteado que representa la salida a Internet vía NAT.",
        "evidencia": "Descripción del diagrama: la ruta punteada desde la subred privada sube por el pasillo central hasta el NAT Gateway.",
        "esperado": {"respaldada": True, "razon": "Coincide con la descripción de la figura."},
    },
]
