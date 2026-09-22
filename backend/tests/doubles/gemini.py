"""Doble determinista de Gemini (issue #10, referencia §12 y §7.5).

Por qué existe: las pruebas ordinarias NO deben gastar cuota del proveedor
ni depender de la red (§12.3: "Las pruebas ordinarias usan MOCK_OCI=1 y
simulación explícita de Gemini"). Este doble imita las tres capacidades
que el proyecto le pide a Gemini — generar texto, verificar afirmaciones
contra evidencia y producir embeddings — con dos garantías que el
proveedor real no da:

1. DETERMINISMO: misma entrada => misma salida, siempre. Un test que usa
   el doble no puede fallar "a veces".
2. SCRIPTABILIDAD: el test programa de antemano qué respuestas recibirá,
   así que puede construir escenarios exactos (aprobación, rechazo por
   calidad, afirmación no respaldada…).

Activación por entorno: la CI publica MOCK_GEMINI=1 y el conftest raíz lo
replica localmente. Cuando el cliente real llegue (issue #13), su fábrica
enrutará a este doble cuando MOCK_GEMINI=1 — el mismo patrón que
get_storage_provider() usa para MOCK_OCI. La suite REAL (Issue 54) es la
única que toca Gemini de verdad.

Cómo se usa en un test:

    doble = DobleGemini()
    doble.programar_generacion("Texto aprobado con cita.")
    respuesta = await doble.generar("prompt...")   # devuelve ese texto
    vectores = await doble.embed(["una frase"])    # siempre el mismo vector

Los EMBEDDINGS se derivan del hash SHA-256 del texto (no de un generador
pseudoaleatorio de Python): la construcción es estable ante versiones y
plataformas, y está normalizada (norma L2 = 1), que es lo que ChromaDB
espera para que la similitud coseno sea comparar productos punto.
"""

from __future__ import annotations

import hashlib
import math
from collections import deque


class DobleGemini:
    """Falso cliente Gemini con respuestas programables y embeddings estables."""

    def __init__(self, dimension_embeddings: int = 768) -> None:
        # 768 es EMBEDDING_DIMENSIONS del Apéndice A.
        self.dimension_embeddings = dimension_embeddings
        self._generaciones: deque[str] = deque()
        self._veredictos: deque[dict] = deque()
        # Contadores de uso: permiten que un test afirme CUÁNTAS llamadas
        # consumió (el presupuesto de §7.5 se prueba contando, no adivinando).
        self.llamadas_generacion = 0
        self.llamadas_verificacion = 0
        self.llamadas_embeddings = 0

    # ------------------------- programación -------------------------

    def programar_generacion(self, *respuestas: str) -> None:
        """Encola las próximas respuestas de `generar`, en orden.

        Si un test no programa nada, `generar` devuelve una respuesta
        neutral que delata el descuido (mejor un texto obvio que un falso
        positivo).
        """
        self._generaciones.extend(respuestas)

    def programar_veredicto(self, respaldada: bool, razon: str = "") -> None:
        """Encola el próximo veredicto de `verificar_afirmacion`."""
        self._veredictos.append({"respaldada": respaldada, "razon": razon})

    def reset(self) -> None:
        """Vuelve al estado inicial (respuestas y contadores en cero)."""
        self._generaciones.clear()
        self._veredictos.clear()
        self.llamadas_generacion = 0
        self.llamadas_verificacion = 0
        self.llamadas_embeddings = 0

    # ------------------------- "API" del doble -------------------------

    async def generar(self, prompt: str, *, modelo: str | None = None) -> str:
        """Devuelve la próxima respuesta programada (async: la API real lo es)."""
        self.llamadas_generacion += 1
        if self._generaciones:
            return self._generaciones.popleft()
        return "[doble-gemini: respuesta no programada por el test]"

    async def verificar_afirmacion(self, afirmacion: str, evidencia: str) -> dict:
        """Devuelve el próximo veredicto programado como dict {respaldada, razon}.

        La forma imita la salida estructurada que el verificador de
        fidelidad (issue #24) le pedirá al modelo real.
        """
        self.llamadas_verificacion += 1
        if self._veredictos:
            return dict(self._veredictos.popleft())
        # Sin veredicto programado: conservador. No respaldar por defecto
        # obliga a los tests a declarar lo que esperan.
        return {"respaldada": False, "razon": "doble sin veredicto programado"}

    async def embed(self, textos: list[str]) -> list[list[float]]:
        """Embeddings deterministas derivados del SHA-256 de cada texto.

        Construcción: para la componente i se hashea "<texto>:<i>", se toma
        un entero de 8 bytes y se mapea a [-1, 1); al final el vector se
        normaliza (norma L2 = 1). Propiedades útiles para los tests:

        - mismo texto => vector idéntico (similitud coseno exactamente 1);
        - textos distintos => componentes independientes (coseno ~ 0);
        - la norma unitaria es lo que ChromaDB espera por defecto.
        """
        self.llamadas_embeddings += 1
        vectores = [self._vector_de(texto) for texto in textos]
        return vectores

    # ------------------------- internals -------------------------

    def _vector_de(self, texto: str) -> list[float]:
        vector = []
        for i in range(self.dimension_embeddings):
            digesto = hashlib.sha256(f"{texto}:{i}".encode("utf-8")).digest()
            entero = int.from_bytes(digesto[:8], "big")
            # Mapa de [0, 2^64) a [-1, 1): entero/2^63 - 1.
            vector.append(entero / (2**63) - 1.0)
        norma = math.sqrt(sum(componente**2 for componente in vector))
        return [componente / norma for componente in vector]
