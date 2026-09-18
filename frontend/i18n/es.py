"""Catálogo ESPAÑOL (latinoamericano) de la interfaz — idioma por defecto.

Convención de claves por área (documentada también en i18n/__init__.py):

- `comun.*`            botones y términos transversales.
- `nav.*`              navegación principal.
- `onboarding.*`       creación de espacio, código de recuperación.
- `sidebar.*`          panel de parámetros de generación.
- `upload.*`           carga de documentos.
- `generar.*`          disparo y seguimiento de generaciones.
- `estados.trabajo.*` / `estados.documento.*`  etiquetas HUMANAS de los
   estados: los códigos de máquina (queued, ready...) viajan por la API y
   NUNCA se muestran crudos (§17.3).
- `perfil.*` `formato.*` `nicho.*` `detalle.*` `idioma.*`
   etiquetas de los enums del contrato (#03), separadas del valor de
   máquina que viaja por la red.
- `quiz.*` `chat.*` `glosario.*` `progreso.*` `exportar.*` `historial.*`
   cada función de la app.
- `errors.*`           mensajes por código ESTABLE de la API (§7.3): la
   clave coincide con el ErrorCode del backend.
- `a11y.*`             textos de accesibilidad (§12.4).
- `almacenamiento.*`   rótulos de proveniencia del storage (§8.2).

Reglas de redacción (§17.1): español latinoamericano; portugués en pt-BR;
inglés técnico claro sin regionalismos. Cambiar el idioma de la UI NO
regenera materiales; el idioma del CONTENIDO viaja aparte (idioma_salida).
"""

CATALOGO_ES: dict[str, str] = {
    # ------------------------- común -------------------------
    "comun.app_nombre": "NuevaMente",
    "comun.cargando": "Cargando…",
    "comun.error_inesperado": "Ocurrió un error inesperado.",
    "comun.reintentar": "Reintentar",
    "comun.cancelar": "Cancelar",
    "comun.cerrar": "Cerrar",
    "comun.guardar": "Guardar",
    "comun.copiar": "Copiar",
    "comun.copiado": "Copiado",
    "comun.descargar": "Descargar",
    "comun.si": "Sí",
    "comun.no": "No",
    "comun.atras": "Atrás",
    "comun.siguiente": "Siguiente",
    "comun.vacio": "Todavía no hay nada por acá.",
    # ------------------------- navegación -------------------------
    "nav.inicio": "Inicio",
    "nav.documentos": "Documentos",
    "nav.generar": "Generar",
    "nav.historial": "Historial",
    "nav.progreso": "Progreso",
    "nav.glosario": "Glosario",
    "nav.configuracion": "Configuración",
    # ------------------------- onboarding -------------------------
    "onboarding.bienvenida_titulo": "Bienvenido a NuevaMente",
    "onboarding.bienvenida_descripcion": "Convertí documentación técnica en material de estudio adaptado a vos.",
    "onboarding.crear_espacio": "Crear mi espacio de estudio",
    "onboarding.codigo_titulo": "Guardá tu código de recuperación",
    "onboarding.codigo_descripcion": "Con este código podés volver a entrar a tu espacio desde cualquier dispositivo.",
    "onboarding.codigo_advertencia": "Lo mostramos una sola vez: si lo perdés, no hay forma de recuperarlo por correo.",
    "onboarding.codigo_copiado": "Código copiado al portapapeles",
    "onboarding.descargar_nota": "Descargar nota de recuperación",
    "onboarding.recuperar_titulo": "Recuperar mi espacio",
    "onboarding.recuperar_ayuda": "Pegá el código que guardaste al crear el espacio.",
    "onboarding.recuperar_boton": "Entrar con mi código",
    # ------------------------- sidebar -------------------------
    "sidebar.titulo": "Parámetros de adaptación",
    "sidebar.documento": "Documento fuente",
    "sidebar.perfil": "Perfil del destinatario",
    "sidebar.formato": "Formato de salida",
    "sidebar.nicho": "Nicho de aplicación",
    "sidebar.detalle": "Nivel de detalle",
    "sidebar.idioma_salida": "Idioma del contenido",
    "sidebar.alcance": "Alcance",
    "sidebar.alcance_completo": "Documento completo",
    "sidebar.alcance_seccion": "Una sección específica",
    "sidebar.seccion": "Sección",
    "sidebar.generar": "Generar material",
    "sidebar.generando": "Generando…",
    # ------------------------- upload -------------------------
    "upload.titulo": "Cargar un documento",
    "upload.arrastrar": "Arrastrá acá un PDF, Markdown o TXT",
    "upload.seleccionar": "o seleccioná un archivo",
    "upload.formatos_soportados": "Formatos: PDF, Markdown (.md) y texto (.txt)",
    "upload.limite": "Hasta 20 MB (PDF) o 5 MB (texto) y 100 páginas",
    "upload.confirmar_publico": "Confirmo que el documento es público o que estoy autorizado a procesarlo",
    "upload.procesando": "Procesando documento…",
    "upload.listo": "Documento listo para usar",
    "upload.fallido": "No pudimos procesar el documento",
    "upload.texto_titulo": "Título del texto",
    "upload.texto_contenido": "Pegá el contenido del texto",
    "upload.enviar_texto": "Cargar como documento de texto",
    # ------------------------- generación -------------------------
    "generar.iniciando": "Preparando la generación…",
    "generar.en_cola": "En cola",
    "generar.posicion": "Posición en la fila: {posicion}",
    "generar.etapa": "Etapa: {etapa}",
    "generar.intento": "Intento {numero} de {total}",
    "generar.tiempo_estimado": "Tiempo estimado restante: {minutos} min",
    "generar.rechazado_calidad": "El material no superó el control de calidad",
    "generar.rechazado_detalle": "Motivo: {motivo}. Podés volver a intentarlo con otra combinación de parámetros.",
    "generar.cancelar": "Cancelar generación",
    "generar.reintentar": "Volver a generar",
    # ------------------------- estados (etiquetas humanas) -------------------------
    "estados.trabajo.queued": "En cola",
    "estados.trabajo.running": "Procesando",
    "estados.trabajo.completed": "Completado",
    "estados.trabajo.rejected_quality": "Rechazado por calidad",
    "estados.trabajo.failed": "Fallido",
    "estados.trabajo.cancelled": "Cancelado",
    "estados.documento.processing": "Procesando",
    "estados.documento.ready": "Listo",
    "estados.documento.failed": "Fallido",
    # ------------------------- enums del contrato -------------------------
    "perfil.principiante": "Principiante / Transición de carrera",
    "perfil.junior_ssr": "Desarrollador Junior / Semi Senior",
    "perfil.lider_tecnico": "Líder Técnico / Arquitecto",
    "perfil.ejecutivo": "Gestor / Ejecutivo (no técnico)",
    "formato.tutorial": "Guía práctica paso a paso",
    "formato.flashcards": "Flashcards de memorización",
    "formato.quiz": "Quiz interactivo con justificaciones",
    "formato.resumen_ejecutivo": "Resumen ejecutivo (TL;DR)",
    "formato.guion_clase": "Guion de clase / video",
    "nicho.fintech": "Fintech",
    "nicho.salud": "Salud",
    "nicho.ecommerce": "E-commerce",
    "nicho.general": "General",
    "detalle.didactico": "Didáctico / Conceptual",
    "detalle.practico": "Práctico / Orientado a código",
    "detalle.tecnico_profundo": "Técnico profundo / Arquitectura",
    "idioma.es": "Español",
    "idioma.en": "English",
    "idioma.pt": "Português (BR)",
    # ------------------------- quiz -------------------------
    "quiz.titulo": "Quiz de comprensión",
    "quiz.pregunta": "Pregunta {numero} de {total}",
    "quiz.responder": "Responder",
    "quiz.siguiente_pregunta": "Siguiente pregunta",
    "quiz.correcto": "¡Correcto!",
    "quiz.incorrecto": "No es la opción correcta",
    "quiz.respuesta_correcta": "La respuesta correcta era: {opcion}",
    "quiz.justificacion": "Por qué",
    "quiz.ver_fuente": "Ver la fuente",
    "quiz.resultado": "Resultado: {aciertos} de {total}",
    "quiz.reintentar": "Intentar de nuevo",
    # ------------------------- chat -------------------------
    "chat.titulo": "Preguntale al documento",
    "chat.placeholder": "Escribí tu pregunta sobre el documento…",
    "chat.enviar": "Enviar",
    "chat.pensando": "Buscando en el documento…",
    "chat.abstencion": "El documento no contiene información suficiente para responder esto con respaldo.",
    "chat.ver_cita": "Ver la cita",
    # ------------------------- glosario -------------------------
    "glosario.titulo": "Glosario adaptado",
    "glosario.vacio": "Todavía no generaste un glosario para este documento.",
    "glosario.generar": "Generar glosario",
    "glosario.termino": "Término",
    "glosario.definicion": "Definición",
    # ------------------------- progreso -------------------------
    "progreso.titulo": "Tu avance",
    "progreso.conceptos": "Conceptos revisados",
    "progreso.flashcards": "Flashcards repasadas",
    "progreso.aciertos": "Aciertos de quiz",
    "progreso.primer_intento": "Aciertos al primer intento",
    "progreso.tiempo_restante": "Tiempo de estudio estimado restante",
    # ------------------------- exportación -------------------------
    "exportar.titulo": "Descargar material",
    "exportar.json": "JSON (paquete canónico)",
    "exportar.md": "Markdown didáctico",
    "exportar.pdf": "PDF didáctico",
    "exportar.csv": "CSV de flashcards",
    "exportar.tsv": "TSV de flashcards",
    "exportar.apkg": "Mazo Anki (.apkg)",
    "exportar.solo_aprobado": "Solo el material aprobado puede descargarse",
    # ------------------------- historial -------------------------
    "historial.titulo": "Historial de generaciones",
    "historial.vacio": "Cuando generes material, va a aparecer acá.",
    "historial.comparar": "Comparar",
    "historial.filtros": "Filtros",
    "historial.misma_fuente": "Mismo documento",
    # ------------------------- almacenamiento -------------------------
    "almacenamiento.local_dev": "Almacenamiento local de desarrollo",
    "almacenamiento.oci": "Almacenamiento en la nube (OCI)",
    # ------------------------- calidad -------------------------
    "calidad.panel": "Calidad del material",
    "calidad.anclaje": "Consistencia con la fuente verificada",
    "calidad.verificacion_visual": "Verificación de diagramas",
    # ------------------------- errores por código estable -------------------------
    "errors.titulo": "Algo salió mal",
    "errors.INVALID_REQUEST": "La petición no tiene el formato esperado.",
    "errors.SESSION_INVALID": "Tu sesión venció o no es válida: recuperá el espacio con tu código.",
    "errors.NOT_FOUND": "No encontramos lo que buscás.",
    "errors.IDEMPOTENCY_CONFLICT": "La operación ya estaba registrada con otros datos.",
    "errors.INVALID_STATE": "La operación no aplica al estado actual del recurso.",
    "errors.DOCUMENT_TOO_LARGE": "El documento supera el tamaño permitido.",
    "errors.EXPORT_INCOMPATIBLE": "Ese formato de exportación no aplica a este material.",
    "errors.VALIDATION_ERROR": "Algún campo del formulario tiene un valor inválido.",
    "errors.QUEUE_FULL": "La cola está llena: probá de nuevo en unos minutos.",
    "errors.RATE_LIMITED": "Alcanzaste el límite de uso: esperá un momento.",
    "errors.RECOVERY_LOCKED": "Demasiados intentos de recuperación: esperá un minuto.",
    "errors.INTERNAL": "Error interno. Si persiste, reportá el identificador de la solicitud.",
    "errors.STORAGE_UNAVAILABLE": "El almacenamiento no está disponible ahora.",
    "errors.PROVIDER_UNAVAILABLE": "El proveedor de IA no está disponible ahora.",
    # ------------------------- accesibilidad -------------------------
    "a11y.ir_al_contenido": "Ir al contenido principal",
    "a11y.indicador_carga": "Cargando contenido, aguardá un momento",
    "a11y.barra_progreso": "Progreso de la generación",
    "a11y.estado_trabajo": "Estado del trabajo: {estado}",
    "a11y.resultado_quiz": "Respuesta {resultado}",
    "a11y.navegacion_teclado": "Usá Tab para navegar y Enter para activar",
}
