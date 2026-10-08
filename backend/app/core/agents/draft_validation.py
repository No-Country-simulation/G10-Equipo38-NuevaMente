"""Validación compartida del borrador y sus citas (Writer/Critic)."""

import json

from pydantic import ValidationError

from app.core.agents.graph_state import BorradorPedagogico, EstadoGrafo
from app.core.agents.prompts import modelo_borrador
from app.schemas.pedagogical import Referencia


class BorradorInvalidoError(ValueError):
    """Corrección pedagógica de estructura/citas, distinta del retry técnico."""


def _sin_duplicados(pares):
    resultado = {}
    for clave, valor in pares:
        if clave in resultado:
            raise BorradorInvalidoError("El JSON contiene claves duplicadas; entregar un objeto sin duplicados.")
        resultado[clave] = valor
    return resultado


def _constante_invalida(valor):
    raise BorradorInvalidoError("El JSON no admite NaN ni valores infinitos.")


def parsear_json_estricto(texto: str):
    """Rechaza claves duplicadas, NaN/inf y cercas Markdown en cualquier rol."""
    return json.loads(texto, object_pairs_hook=_sin_duplicados, parse_constant=_constante_invalida)


def validar_borrador(texto: str, estado: EstadoGrafo) -> tuple[BorradorPedagogico, list[Referencia]]:
    try:
        datos = parsear_json_estricto(texto)
        validado = modelo_borrador(estado.parametros.formato_salida).model_validate_json(
            json.dumps(datos, ensure_ascii=False, allow_nan=False), strict=True
        )
    except (ValidationError, ValueError, TypeError, RecursionError):
        raise BorradorInvalidoError(
            "Entregar JSON estricto con contenido y metadatos completos según el schema."
        ) from None
    solicitud = estado.parametros
    metadatos = validado.metadatos
    esperados = {
        "perfil_aplicado": solicitud.perfil_destinatario,
        "formato_generado": solicitud.formato_salida,
        "nicho_sector": solicitud.nicho_sector,
        "nivel_detalle": solicitud.nivel_detalle,
        "idioma_salida": solicitud.idioma_salida,
        "idioma_origen": estado.idioma_origen,
    }
    if any(getattr(metadatos, campo) != valor for campo, valor in esperados.items()):
        raise BorradorInvalidoError(
            "Los metadatos deben respetar perfil, formato, nicho, detalle e idiomas solicitados."
        )
    if metadatos.alcance.model_dump(exclude={"secciones_cubiertas"}) != solicitud.alcance.model_dump():
        raise BorradorInvalidoError("Conservar el alcance solicitado sin sustituir el documento o la sección.")
    if set(metadatos.alcance.secciones_cubiertas or []) != set(estado.secciones_cubiertas):
        raise BorradorInvalidoError("Declarar únicamente la cobertura recuperada por Researcher.")

    permitidas = {item.chunk.chunk_id: item.como_referencia() for item in estado.evidencia}
    referencias = {}
    contenido = validado.contenido_adaptado.model_dump(mode="json")

    def recorrer(valor):
        if isinstance(valor, dict):
            for campo, item in valor.items():
                if campo == "referencias":
                    for indice, cita in enumerate(item):
                        referencia = Referencia.model_validate(cita)
                        original = permitidas.get(referencia.chunk_id)
                        if original is None:
                            raise BorradorInvalidoError("Usar solo chunk_id presentes en la evidencia autorizada.")
                        if (referencia.pagina is not None and referencia.pagina != original.pagina) or (
                            referencia.seccion is not None and referencia.seccion != original.seccion
                        ):
                            raise BorradorInvalidoError(
                                "Las ubicaciones de las citas deben coincidir con la evidencia."
                            )
                        item[indice] = original.model_dump(mode="json")
                        referencias[original.chunk_id] = original
                else:
                    recorrer(item)
        elif isinstance(valor, list):
            for item in valor:
                recorrer(item)

    recorrer(contenido)
    metadatos.alcance.secciones_cubiertas = list(estado.secciones_cubiertas)
    return BorradorPedagogico(contenido_adaptado=contenido, metadatos=metadatos), list(referencias.values())
