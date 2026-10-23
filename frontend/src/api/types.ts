export type RecipientProfile = 'principiante' | 'junior_ssr' | 'lider_tecnico' | 'ejecutivo';
export type PedagogicalFormat = 'tutorial' | 'flashcards' | 'quiz' | 'resumen_ejecutivo' | 'guion_clase';
export type IndustryNiche = 'fintech' | 'salud' | 'ecommerce' | 'general';
export type DetailLevel = 'didactico' | 'practico' | 'tecnico_profundo';
export type OutputLanguage = 'es' | 'en' | 'pt';
export type JobStatus = 'queued' | 'running' | 'completed' | 'rejected_quality' | 'failed' | 'cancelled';
export type DocumentStatus = 'processing' | 'ready' | 'failed';
export type ExportFormat = 'json' | 'md' | 'pdf' | 'csv' | 'tsv' | 'apkg';
export type TipoAlcance = 'documento_completo' | 'seccion' | 'multi_documento';

export const PERFILES: readonly RecipientProfile[] = [
  'principiante',
  'junior_ssr',
  'lider_tecnico',
  'ejecutivo',
] as const;

export const FORMATOS: readonly PedagogicalFormat[] = [
  'tutorial',
  'flashcards',
  'quiz',
  'resumen_ejecutivo',
  'guion_clase',
] as const;

export const NICHOS: readonly IndustryNiche[] = [
  'fintech',
  'salud',
  'ecommerce',
  'general',
] as const;

export const NIVELES_DETALLE: readonly DetailLevel[] = [
  'didactico',
  'practico',
  'tecnico_profundo',
] as const;

export const IDIOMAS_SALIDA: readonly OutputLanguage[] = ['es', 'en', 'pt'] as const;

export const FORMATOS_EXPORTACION: readonly ExportFormat[] = [
  'json',
  'md',
  'pdf',
  'csv',
  'tsv',
  'apkg',
] as const;

export const ESTADOS_TRABAJO: readonly JobStatus[] = [
  'queued',
  'running',
  'completed',
  'rejected_quality',
  'failed',
  'cancelled',
] as const;

export interface SessionResponse {
  workspace_id: string;
  token: string;
}

export interface WorkspaceResponse extends SessionResponse {
  recovery_code: string;
}

export interface RotatedCodeResponse {
  recovery_code: string;
  token: string;
}

export interface WorkspaceStatusResponse {
  workspace_id?: string;
  expira_en: string;
  ultima_actividad_en?: string;
  uso: {
    documentos: number;
    limite_documentos: number;
    generaciones: number;
    limite_generaciones: number;
  };
  almacenamiento: {
    alerta: boolean;
    escrituras_bloqueadas: boolean;
    provider?: 'oci' | 'mock';
  };
}

export interface TextDocumentRequest {
  documento_titulo: string;
  documento_contenido: string;
}

export interface UploadResponse {
  document_id: string;
  status: 'processing';
  job_id: string;
  status_url: string;
  events_url: string;
  cancel_url: string;
}

export interface SeccionDocumental {
  seccion_id: string;
  titulo: string;
  linea_inicio: number;
  linea_fin: number;
}

export interface CoberturaDocumento {
  paginas_procesadas: number[];
  paginas_omitidas: number[];
  paginas_visuales_pendientes: number[];
  secciones_procesadas: SeccionDocumental[];
  secciones_omitidas: SeccionDocumental[] | null;
  secciones_visuales_pendientes: SeccionDocumental[] | null;
}

export interface MetadataDocumento {
  extension: string;
  tamano_bytes: number;
  creado_en?: string;
  cantidad_paginas: number | null;
  cantidad_tokens: number | null;
}

export interface DocumentoResponse {
  document_id: string;
  titulo: string;
  status: DocumentStatus;
  metadata: MetadataDocumento | null;
  cobertura: CoberturaDocumento | null;
  error: { code?: string | null; message?: string } | null;
}

export interface DocumentosResponse {
  items: DocumentoResponse[];
  next_cursor: string | null;
}

export interface AlcanceSolicitud {
  tipo: TipoAlcance;
  seccion_id?: string;
  document_ids?: string[];
}

export interface Alcance extends AlcanceSolicitud {
  secciones_cubiertas?: string[] | null;
}

export interface GenerateRequest {
  modo_generacion?: 'multiagente' | 'basico';
  document_id: string;
  document_ids?: string[];
  perfil_destinatario: RecipientProfile;
  formato_salida: PedagogicalFormat;
  nicho_sector: IndustryNiche;
  nivel_detalle: DetailLevel;
  idioma_salida: OutputLanguage;
  alcance: AlcanceSolicitud;
}

export interface Referencia {
  chunk_id: string;
  pagina?: number | null;
  seccion?: string | null;
  document_id?: string | null;
}

export interface EvaluacionCalidad {
  anclaje_fuente_score: number | null;
  cantidad_afirmaciones: number;
  cantidad_respaldadas: number;
  estado_evaluacion: 'aprobada' | 'requiere_revision' | 'no_evaluable';
  claridad_pedagogica: 'alta' | 'media' | 'baja';
  adecuacion_perfil: 'alta' | 'media' | 'baja';
  cobertura_objetivos: 'completa' | 'parcial';
  coherencia_didactica: 'alta' | 'media' | 'baja';
  verificacion_visual: 'no_aplica' | 'aprobada' | 'insuficiente';
  observaciones?: string | null;
  razones_bloqueo?: string[] | null;
}

export interface FlashcardItem {
  id: string;
  frente: string;
  dorso: string;
  pista_didactica?: string | null;
  etiquetas: string[];
  referencias: Referencia[];
}

export interface FlashcardDeck {
  tipo: 'flashcards';
  titulo: string;
  introduccion_contextualizada?: string | null;
  items: FlashcardItem[];
}

export interface QuizOption {
  option_id: string;
  texto: string;
}

export interface PreguntaVistaEstudiante {
  id: string;
  enunciado: string;
  opciones: QuizOption[];
}

export interface QuizVistaEstudiante {
  tipo: 'quiz';
  titulo: string;
  introduccion_contextualizada?: string | null;
  preguntas: PreguntaVistaEstudiante[];
}

export interface PasoTutorial {
  id: string;
  instruccion: string;
  resultado_esperado: string;
  verificacion: string;
  codigo?: string | null;
  referencias: Referencia[];
}

export interface PracticalTutorial {
  tipo: 'tutorial';
  titulo: string;
  introduccion_contextualizada?: string | null;
  audiencia: string;
  prerrequisitos: string[];
  pasos: PasoTutorial[];
}

export interface ExecutiveSummary {
  tipo: 'resumen_ejecutivo';
  titulo: string;
  introduccion_contextualizada?: string | null;
  puntos_clave: string[];
  impacto_cualitativo: string;
  implicaciones: string[];
  acciones: string[];
  referencias: Referencia[];
}

export interface EscenaGuion {
  id: string;
  duracion_min: number;
  narracion: string;
  puntos_diapositiva: string[];
  pregunta_interactiva?: string | null;
  referencias: Referencia[];
}

export interface VideoLessonScript {
  tipo: 'guion_clase';
  titulo: string;
  introduccion_contextualizada?: string | null;
  objetivos: string[];
  escenas: EscenaGuion[];
}

export type ContenidoEstudiante =
  | FlashcardDeck
  | QuizVistaEstudiante
  | PracticalTutorial
  | ExecutiveSummary
  | VideoLessonScript;

export interface Metadatos {
  perfil_aplicado: RecipientProfile;
  formato_generado: PedagogicalFormat;
  nicho_sector: IndustryNiche;
  nivel_detalle: DetailLevel;
  idioma_origen: 'es' | 'en' | 'pt' | 'mixto';
  idioma_salida: OutputLanguage;
  conceptos_clave: string[];
  prerrequisitos: string[];
  objetivos_aprendizaje: string[];
  tiempo_estimado_estudio_minutos: number;
  alcance: Alcance;
}

export interface DocumentoFuente {
  document_id: string;
  titulo: string;
  hash: string;
  version: string;
  procedencia?: string | null;
}

export interface ReviewedStudentOutput {
  schema_version: '1.0' | '1.1';
  modo_generacion?: 'multiagente';
  generation_id: string;
  status: 'aprobado';
  metadatos: Metadatos;
  documento_fuente: DocumentoFuente;
  documentos_fuente?: DocumentoFuente[];
  contenido_adaptado: ContenidoEstudiante;
  evaluacion_calidad: EvaluacionCalidad;
  referencias: Referencia[];
  created_at: string;
  trazabilidad?: Record<string, unknown>;
  almacenamiento_oci?: {
    bucket: string;
    objeto_id: string;
  };
}

export type BasicStudentOutput = Omit<ReviewedStudentOutput,
  'schema_version' | 'modo_generacion' | 'status' | 'evaluacion_calidad'> & {
  schema_version: '1.1';
  modo_generacion: 'basico';
  status: 'no_evaluado';
  evaluacion_calidad: { estado_evaluacion: 'no_realizada' } & {
    [K in Exclude<keyof EvaluacionCalidad, 'estado_evaluacion'>]: null;
  };
};

export type PedagogicalStudentOutput = ReviewedStudentOutput | BasicStudentOutput;

export interface GenerationJobResponse {
  generation_id: string;
  status: JobStatus;
  status_url?: string;
  events_url?: string;
  cancel_url?: string | null;
  posicion_cola?: number | null;
  contenido?: PedagogicalStudentOutput | null;
  persistencia?: {
    status_upload: 'completado' | 'pendiente' | 'fallido';
    provider: 'oci' | 'mock';
  } | null;
  error?: {
    code: string;
    message?: string;
    details?: Record<string, any>;
  } | null;
}

export interface FuenteDocumentoResponse {
  document_id: string;
  chunk_id: string;
  texto: string;
  pagina: number | null;
  seccion: string | null;
  seccion_id?: string | null;
  source_type?: string | null;
  interpretacion_modelo?: boolean;
  imagen_url?: string | null;
}

export interface QuizAnswerRequest {
  question_id: string;
  option_id: string;
  event_id: string;
}

export interface QuizAnswerResponse {
  correcta: boolean;
  correct_option_id: string;
  justificacion: string;
  referencias: Referencia[];
}

export interface ChatRequest {
  document_id?: string;
  document_ids?: string[];
  pregunta: string;
  perfil_destinatario: RecipientProfile;
  idioma_salida: OutputLanguage;
}

export interface ChatAnswer {
  document_id: string;
  document_ids?: string[];
  document_hash: string;
  documentos_fuente?: DocumentoFuente[];
  pregunta: string;
  perfil_destinatario: RecipientProfile;
  idioma_salida: OutputLanguage;
  estado: 'respondida' | 'fuera_de_alcance';
  respuesta: string;
  referencias: Referencia[];
  evaluacion_calidad: EvaluacionCalidad | null;
}

export interface GlossaryRequest {
  document_id?: string;
  document_ids?: string[];
  perfil_destinatario: RecipientProfile;
  idioma_salida: OutputLanguage;
}

export interface TerminoGlosario {
  id: string;
  termino_original: string;
  es_identificador: boolean;
  traduccion: string | null;
  estado: 'definido' | 'sin_definicion_suficiente';
  definicion: string | null;
  limitacion: string | null;
  referencias: Referencia[];
}

export interface GlossaryOutput {
  schema_version?: '1.0';
  glossary_id: string;
  documento_fuente: DocumentoFuente;
  documentos_fuente?: DocumentoFuente[];
  perfil_destinatario: RecipientProfile;
  idioma_salida: OutputLanguage;
  estado: 'verificado' | 'sin_definiciones' | 'sin_terminos';
  terminos: TerminoGlosario[];
  candidatos_omitidos: number;
  evaluacion_calidad: EvaluacionCalidad | null;
  trazabilidad: Record<string, unknown> | null;
  prompt_version?: string;
  created_at?: string;
}

export interface ProgressResponse {
  version: number;
  conceptos_revisados: number;
  flashcards_vistas: number;
  quiz_intentos: number;
  quiz_aciertos_primer_intento: number;
  quiz_total_primer_intento: number;
  quiz_aciertos_posteriores: number;
  tiempo_estimado_restante_minutos: number;
}
