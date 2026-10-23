import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  BookOpen,
  Download,
  FileText,
  FolderOpen,
  KeyRound,
  Layers,
  LogOut,
  RefreshCw,
  RotateCcw,
  Settings,
  ShieldAlert,
  Sparkles,
  Trash2,
  Upload,
} from 'lucide-react';
import { APIClient, APIError } from './api/client';
import {
  AlcanceSolicitud,
  DetailLevel,
  DocumentoResponse,
  FORMATOS,
  GenerateRequest,
  IDIOMAS_SALIDA,
  IndustryNiche,
  NICHOS,
  NIVELES_DETALLE,
  OutputLanguage,
  PedagogicalFormat,
  PedagogicalStudentOutput,
  PERFILES,
  RecipientProfile,
  SeccionDocumental,
  TipoAlcance,
} from './api/types';
import { OraclePillLogo, OracleWaveCircuitBackdrop } from './assets/OracleVisuals';
import { ErrorBanner } from './components/ErrorBanner';
import { DocumentStatus } from './components/DocumentStatus';
import { ExportDownloads } from './components/ExportDownloads';
import { RecentGenerations } from './components/RecentGenerations';
import { PackageViewer } from './components/FormatRenderers';
import { SourcesDrawer } from './components/SourcesDrawer';
import {
  ChatTutorView,
  GlossaryView,
  HistoryFullView,
  ProgressView,
} from './components/StudyAndHistoryViews';
import {
  DOCUMENTOS_DEMO,
  claveCarga,
  formatearTamano,
  generarEventIdProgreso,
  validarArchivo,
  validarGeneracionCompletada,
  validarPaquete,
  validarRespuestaChat,
  validarTextoPegado,
} from './flows/validators';
import { CATALOGOS, IDIOMA_POR_DEFECTO, t } from './i18n';
import { clearStudyState } from './flows/studyState';
import { isActive, recoverActiveGeneration, useGenerationProgress, validateSnapshot } from './flows/generationProgress';
import { GenerationLiveStatus } from './components/GenerationLiveStatus';

interface GeneracionActivaUI {
  generation_id: string;
  status: string;
  status_url: string;
  events_url: string;
  parametros: GenerateRequest;
  documento_titulo: string;
  seccion_titulo: string | null;
}

export const App: React.FC = () => {
  // Idioma de interfaz (persiste en sessionStorage)
  const [idiomaUi, setIdiomaUi] = useState<OutputLanguage>(() => {
    const saved = sessionStorage.getItem('nm_idioma_ui');
    return saved === 'es' || saved === 'en' || saved === 'pt' ? saved : IDIOMA_POR_DEFECTO;
  });

  // Sesión y Workspace (recovery_code vive SOLO en memoria efímera)
  const [sessionToken, setSessionToken] = useState<string | null>(null);
  const [workspaceId, setWorkspaceId] = useState<string | null>(null);
  const [recoveryCode, setRecoveryCode] = useState<string | null>(null);
  const [mostrandoRecuperacion, setMostrandoRecuperacion] = useState(false);
  const [errorSesionInvalida, setErrorSesionInvalida] = useState(false);
  const [avisoSesion, setAvisoSesion] = useState<string | null>(null);
  const [initError, setInitError] = useState<APIError | null>(null);
  const [globalError, setGlobalError] = useState<APIError | null>(null);

  // Documentos y Demo
  const [documentos, setDocumentos] = useState<DocumentoResponse[]>([]);
  const [trabajosDoc, setTrabajosDoc] = useState<Record<string, any>>({});
  const trabajosDocRef = useRef(trabajosDoc);
  trabajosDocRef.current = trabajosDoc;
  const [docActivoId, setDocActivoId] = useState<string | null>(null);
  const [docActivoDemo, setDocActivoDemo] = useState(false);
  const [docsSeleccionados, setDocsSeleccionados] = useState<string[]>([]);
  const [vistaOnboarding, setVistaOnboarding] = useState<'upload' | 'texto' | 'demo'>('upload');
  const [avisoDocBorrado, setAvisoDocBorrado] = useState(false);
  const [confirmDeleteDocId, setConfirmDeleteDocId] = useState<string | null>(null);

  // Presupuesto y Estado del Espacio
  const [wsStatus, setWsStatus] = useState<any | null>(null);

  // Controles de Generación
  const [genPerfil, setGenPerfil] = useState<RecipientProfile>('principiante');
  const [genFormato, setGenFormato] = useState<PedagogicalFormat>('flashcards');
  const [genNicho, setGenNicho] = useState<IndustryNiche>('general');
  const [genDetalle, setGenDetalle] = useState<DetailLevel>('didactico');
  const [genIdiomaSalida, setGenIdiomaSalida] = useState<OutputLanguage>('es');
  const [genModo, setGenModo] = useState<'multiagente' | 'basico'>('multiagente');
  const [genAlcance, setGenAlcance] = useState<TipoAlcance>('documento_completo');
  const [genSeccionId, setGenSeccionId] = useState<string>('');
  const [confirmModalPending, setConfirmModalPending] = useState<{
    peticion: GenerateRequest;
    idempotencyKey: string;
    documentoTitulo: string;
    seccionTitulo: string | null;
  } | null>(null);
  const lastGenAttemptRef = useRef<{ peticionJson: string; key: string } | null>(null);

  // Navegación Principal y Generación Activa
  const [generacionActiva, setGeneracionActiva] = useState<GeneracionActivaUI | null>(null);
  const activeGenRef = useRef(generacionActiva?.generation_id);
  activeGenRef.current = generacionActiva?.generation_id;
  const [activeJobData, setActiveJobData] = useState<any | null>(null);
  const [mostrandoDocumentos, setMostrandoDocumentos] = useState(false);
  const [activeTab, setActiveTab] = useState<'resultado' | 'tutor' | 'glosario' | 'progreso'>('resultado');
  const [historialAbierto, setHistorialAbierto] = useState(false);
  const [ejemploDemoId, setEjemploDemoId] = useState<string | null>(null);
  const [accionConfirmar, setAccionConfirmar] = useState<'rotar' | 'borrar' | null>(null);

  // Progreso e idempotencia de eventos
  const [confirmedConcepts, setConfirmedConcepts] = useState<Record<string, boolean>>({});
  const [pendingProgressEvents, setPendingProgressEvents] = useState<
    Array<{ event_id: string; datos: Record<string, any> }>
  >([]);

  const api = useMemo(() => new APIClient({ idiomaUi }), [idiomaUi]);
  const authRef = useRef(sessionToken);
  authRef.current = sessionToken;
  const selectionRef = useRef({ id: docActivoId, demo: docActivoDemo });
  selectionRef.current = { id: docActivoId, demo: docActivoDemo };

  useEffect(() => {
    if (!confirmModalPending && !accionConfirmar) return;
    const modal = document.querySelector<HTMLElement>('[role="dialog"]');
    if (!modal) return;
    const previous = document.activeElement as HTMLElement | null;
    const controls = () => [...modal.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), a[href]')];
    controls().at(-1)?.focus();
    const trap = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); setConfirmModalPending(null); setAccionConfirmar(null); }
      if (event.key === 'Tab') {
        const items = controls();
        if (event.shiftKey && document.activeElement === items[0]) { event.preventDefault(); items.at(-1)?.focus(); }
        if (!event.shiftKey && document.activeElement === items.at(-1)) { event.preventDefault(); items[0]?.focus(); }
      }
    };
    document.addEventListener('keydown', trap);
    return () => { document.removeEventListener('keydown', trap); if (previous?.isConnected) previous.focus(); };
  }, [Boolean(confirmModalPending), accionConfirmar]);

  useEffect(() => {
    sessionStorage.setItem('nm_idioma_ui', idiomaUi);
  }, [idiomaUi]);

  useEffect(() => {
    sessionStorage.removeItem('nm_session_token');
    sessionStorage.removeItem('nm_workspace_id');
  }, []);

  const limpiarSesionLocal = useCallback(() => {
    authRef.current = null;
    activeGenRef.current = undefined;
    creacionPendiente.current = null;
    clearStudyState();
    setSessionToken(null);
    setWorkspaceId(null);
    setRecoveryCode(null);
    setDocumentos([]);
    setTrabajosDoc({});
    setDocActivoId(null);
    setDocActivoDemo(false);
    setGeneracionActiva(null);
    setActiveJobData(null);
    setHistorialAbierto(false);
    setEjemploDemoId(null);
    setConfirmedConcepts({});
    setPendingProgressEvents([]);
    sessionStorage.removeItem('nm_session_token');
    sessionStorage.removeItem('nm_workspace_id');
  }, []);

  const manejarSesionInvalida = useCallback(() => {
    limpiarSesionLocal();
    setMostrandoRecuperacion(true);
    setErrorSesionInvalida(true);
  }, [limpiarSesionLocal]);

  // Creación automática de espacio anónimo
  const creacionPendiente = useRef<Promise<any> | null>(null);
  const inicializarEspacio = useCallback(async () => {
    if (sessionToken || mostrandoRecuperacion) return;
    setInitError(null);
    const pending = creacionPendiente.current ??= api.createWorkspace();
    try {
      const res = await pending;
      if (creacionPendiente.current !== pending) return;
      setSessionToken(res.token);
      setWorkspaceId(res.workspace_id);
      setRecoveryCode(res.recovery_code);
    } catch (err) {
      if (creacionPendiente.current !== pending) return;
      creacionPendiente.current = null;
      if (err instanceof APIError) setInitError(err);
    }
  }, [api, sessionToken, mostrandoRecuperacion]);

  useEffect(() => {
    inicializarEspacio();
  }, [inicializarEspacio]);

  // Sincronización de documentos y presupuesto
  const sincronizarEspacio = useCallback(async () => {
    if (!sessionToken) return;
    try {
      const [docsPage, statusRes] = await Promise.all([
        api.getDocuments(sessionToken, { limit: 100 }),
        api.workspaceStatus(sessionToken),
      ]);
      if (authRef.current !== sessionToken) return;
      const docs: DocumentoResponse[] = docsPage?.items || [];
      setDocumentos(docs);
      setWsStatus(statusRes);
      await Promise.all(Object.entries(trabajosDocRef.current).map(async ([docId, seg]) => {
        if (!docs.some((doc) => doc.document_id === docId && doc.status === 'processing')) return;
        const [job, progreso] = await Promise.all([
          api.getJob(sessionToken, seg.jobId), api.readJobProgress(sessionToken, seg.jobId, seg.cursor),
        ]);
        if (authRef.current !== sessionToken) return;
        setTrabajosDoc((prev) => ({ ...prev, [docId]: {
          ...seg, estado: job.status, progreso: progreso?.data ?? seg.progreso,
          cursor: progreso?.id ?? seg.cursor,
        } }));
      }));

      if (!selectionRef.current.demo) {
        const actual = docs.find((d) => d.document_id === selectionRef.current.id && d.status === 'ready');
        if (!actual) {
          const primerReady = docs.find((d) => d.status === 'ready');
          setDocActivoId(primerReady ? primerReady.document_id : null);
        }
      }
    } catch (err) {
      if (authRef.current !== sessionToken) return;
      if (err instanceof APIError) {
        if (err.statusCode === 401) manejarSesionInvalida();
        else setGlobalError(err);
      }
    }
  }, [api, sessionToken, docActivoId, docActivoDemo, manejarSesionInvalida]);

  useEffect(() => {
    sincronizarEspacio();
  }, [sincronizarEspacio]);

  // Sondeo de documentos en procesamiento cada 2s
  useEffect(() => {
    if (!sessionToken) return;
    const hayProcesando = documentos.some((d) => d.status === 'processing');
    if (!hayProcesando) return;
    let stopped = false;
    let timer = setTimeout(async function poll() {
      await sincronizarEspacio();
      if (!stopped) timer = setTimeout(poll, 2000);
    }, 2000);
    return () => { stopped = true; clearTimeout(timer); };
  }, [sessionToken, documentos, sincronizarEspacio]);

  // Sondeo de generación activa
  const consultarGeneracionActiva = useCallback(async () => {
    if (!sessionToken || !generacionActiva) return;
    try {
      const job = await api.getGeneration(sessionToken, generacionActiva.generation_id);
      if (authRef.current !== sessionToken || activeGenRef.current !== generacionActiva.generation_id) return;
      validateSnapshot(job, generacionActiva.generation_id);
      setActiveJobData(job);
      if (!['queued', 'running'].includes(job.status)) await sincronizarEspacio();
    } catch (err) {
      if (authRef.current !== sessionToken || activeGenRef.current !== generacionActiva.generation_id) return;
      if (err instanceof APIError) {
        if (err.statusCode === 401) manejarSesionInvalida();
        else setGlobalError(err);
      }
    }
  }, [api, sessionToken, generacionActiva, manejarSesionInvalida, sincronizarEspacio]);

  const generationStream = useGenerationProgress({
    api, token: sessionToken, generationId: generacionActiva?.generation_id ?? null,
    enabled: !!generacionActiva && (!activeJobData || isActive(activeJobData.status)),
    onSnapshot: (job) => {
      if (authRef.current !== sessionToken || activeGenRef.current !== job.generation_id) return;
      setActiveJobData(job);
      if (!isActive(job.status)) void sincronizarEspacio();
    },
    onSessionInvalid: manejarSesionInvalida,
  });

  useEffect(() => {
    if (!sessionToken) return;
    let active = true;
    recoverActiveGeneration(api, sessionToken).then(generation => {
      if (!active || authRef.current !== sessionToken || activeGenRef.current || !generation) return;
      activeGenRef.current = generation.generation_id;
      setGeneracionActiva(generation);
      setActiveJobData(null);
      selectionRef.current = { id: generation.parametros.document_id, demo: false };
      setDocActivoId(generation.parametros.document_id);
      setDocActivoDemo(false);
      setGenPerfil(generation.parametros.perfil_destinatario);
      setGenFormato(generation.parametros.formato_salida);
      setGenNicho(generation.parametros.nicho_sector);
      setGenDetalle(generation.parametros.nivel_detalle);
      setGenIdiomaSalida(generation.parametros.idioma_salida);
      setGenModo(generation.parametros.modo_generacion ?? 'multiagente');
      setGenAlcance(generation.parametros.alcance.tipo);
      setGenSeccionId(generation.parametros.alcance.seccion_id ?? '');
      setMostrandoDocumentos(false);
      setHistorialAbierto(false);
      setEjemploDemoId(null);
      setActiveTab('resultado');
    }).catch(error => {
      if (!active || authRef.current !== sessionToken) return;
      if (error instanceof APIError && error.statusCode === 401) manejarSesionInvalida();
      else if (error instanceof APIError) setGlobalError(error);
    });
    return () => { active = false; };
  }, [api, sessionToken, manejarSesionInvalida]);

  const docsReady = useMemo(
    () => documentos.filter((d) => d.status === 'ready'),
    [documentos]
  );

  const docSeleccionado = useMemo(
    () => {
      if (docActivoDemo) return null;
      if (docActivoId) {
        const found = docsReady.find((d) => d.document_id === docActivoId);
        if (found) return found;
      }
      if (docsSeleccionados.length > 0) {
        const found = docsReady.find((d) => d.document_id === docsSeleccionados[0]);
        if (found) return found;
      }
      return null;
    },
    [docsReady, docActivoId, docActivoDemo, docsSeleccionados]
  );

  const seccionesDisponibles: SeccionDocumental[] = useMemo(
    () => (docsSeleccionados.length <= 1 ? docSeleccionado?.cobertura?.secciones_procesadas || [] : []),
    [docSeleccionado, docsSeleccionados.length]
  );

  useEffect(() => {
    if (docActivoId && !docActivoDemo) {
      setDocsSeleccionados((prev) => {
        if (prev.length <= 1) {
          return [docActivoId];
        }
        if (!prev.includes(docActivoId)) {
          return [docActivoId, ...prev].slice(0, 5);
        }
        return prev;
      });
    }
  }, [docActivoId, docActivoDemo]);

  useEffect(() => {
    setDocsSeleccionados((prev) => {
      const readyIds = new Set(docsReady.map((d) => d.document_id));
      const filtrados = prev.filter((id) => readyIds.has(id));
      if (filtrados.length !== prev.length) {
        return filtrados;
      }
      return prev;
    });
  }, [docsReady]);

  const toggleDocSeleccionado = (docId: string) => {
    setDocsSeleccionados((prev) => {
      if (prev.includes(docId)) {
        const nuevo = prev.filter((id) => id !== docId);
        if (nuevo.length === 1) {
          setDocActivoId(nuevo[0]);
        }
        return nuevo;
      } else {
        if (prev.length >= 5) {
          return prev;
        }
        const nuevo = [...prev, docId];
        if (nuevo.length === 1) {
          setDocActivoId(docId);
        }
        return nuevo;
      }
    });
  };

  const seleccionarTodosDocs = () => {
    const ids = docsReady.slice(0, 5).map((d) => d.document_id);
    setDocsSeleccionados(ids);
    if (ids.length > 0 && (!docActivoId || !ids.includes(docActivoId))) {
      setDocActivoId(ids[0]);
    }
  };

  const limpiarSeleccionDocs = () => {
    if (docActivoId) {
      setDocsSeleccionados([docActivoId]);
    } else if (docsReady[0]) {
      setDocsSeleccionados([docsReady[0].document_id]);
      setDocActivoId(docsReady[0].document_id);
    } else {
      setDocsSeleccionados([]);
    }
  };

  useEffect(() => {
    if (seccionesDisponibles.length === 0 || docsSeleccionados.length > 1) {
      setGenAlcance('documento_completo');
      setGenSeccionId('');
    } else if (genAlcance === 'seccion' && !seccionesDisponibles.some((s) => s.seccion_id === genSeccionId)) {
      setGenSeccionId(seccionesDisponibles[0].seccion_id);
    }
  }, [seccionesDisponibles, genAlcance, genSeccionId, docsSeleccionados.length]);

  const prepararConfirmacionGenerar = () => {
    const docIds = docsSeleccionados.length > 0
      ? docsSeleccionados
      : (docSeleccionado ? [docSeleccionado.document_id] : []);
    if (docIds.length === 0) return;

    const sortedDocIds = [...docIds].sort();
    const esMulti = sortedDocIds.length > 1;
    const primaryDocId = sortedDocIds[0];
    const primaryDoc = docsReady.find((d) => d.document_id === primaryDocId) || docSeleccionado;

    let alcanceObj: AlcanceSolicitud;
    if (esMulti) {
      alcanceObj = { tipo: 'multi_documento', document_ids: sortedDocIds };
    } else if (genAlcance === 'seccion' && genSeccionId) {
      alcanceObj = { tipo: 'seccion', seccion_id: genSeccionId };
    } else {
      alcanceObj = { tipo: 'documento_completo' };
    }

    const peticion: GenerateRequest = {
      modo_generacion: genModo,
      document_id: primaryDocId,
      document_ids: esMulti ? sortedDocIds : undefined,
      perfil_destinatario: genPerfil,
      formato_salida: genFormato,
      nicho_sector: genNicho,
      nivel_detalle: genDetalle,
      idioma_salida: genIdiomaSalida,
      alcance: alcanceObj,
    };
    const peticionJson = JSON.stringify(peticion);
    let key = lastGenAttemptRef.current?.peticionJson === peticionJson
      ? lastGenAttemptRef.current.key
      : `gen-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    lastGenAttemptRef.current = { peticionJson, key };

    const secObj = !esMulti ? seccionesDisponibles.find((s) => s.seccion_id === genSeccionId) : null;
    const docTitulo = esMulti
      ? `${primaryDoc?.titulo || primaryDocId} (+${sortedDocIds.length - 1} fuentes)`
      : (primaryDoc?.titulo || primaryDocId);

    setConfirmModalPending({
      peticion,
      idempotencyKey: key,
      documentoTitulo: docTitulo,
      seccionTitulo: secObj ? secObj.titulo : null,
    });
  };

  const confirmarYEnviarGeneracion = async () => {
    if (!confirmModalPending || !sessionToken) return;
    setGlobalError(null);
    try {
      const resp = await api.createGeneration(
        sessionToken,
        confirmModalPending.peticion,
        confirmModalPending.idempotencyKey
      );
      if (authRef.current !== sessionToken) return;
      setActiveJobData(null);
      activeGenRef.current = resp.generation_id;
      setGeneracionActiva({
        generation_id: resp.generation_id,
        status: resp.status,
        status_url: resp.status_url,
        events_url: resp.events_url,
        parametros: confirmModalPending.peticion,
        documento_titulo: confirmModalPending.documentoTitulo,
        seccion_titulo: confirmModalPending.seccionTitulo,
      });
      setEjemploDemoId(null);
      setMostrandoDocumentos(false);
      setHistorialAbierto(false);
      setActiveTab('resultado');
      setConfirmModalPending(null);
      lastGenAttemptRef.current = null;
    } catch (err) {
      if (authRef.current !== sessionToken) return;
      if (err instanceof APIError) {
        if (err.statusCode === 401) manejarSesionInvalida();
        else setGlobalError(err);
      }
    }
  };

  // Progreso: marcar concepto o flashcard
  const enviarEventoProgreso = async (eventId: string, datos: Record<string, any>) => {
    if (!sessionToken) return;
    try {
      const recibo = await api.recordProgress(sessionToken, { ...datos, event_id: eventId });
      if (authRef.current !== sessionToken) return;
      if (recibo?.event_id !== eventId || !Number.isInteger(recibo.version) || recibo.version < 1) {
        throw new APIError('INVALID_RESPONSE', 'Confirmación de progreso inválida.', 502);
      }
      setPendingProgressEvents((prev) => prev.filter((e) => e.event_id !== eventId));
      if (datos.tipo === 'concepto_revisado') {
        setConfirmedConcepts((prev) => ({
          ...prev,
          [`${datos.document_id}::${datos.concepto}`]: true,
        }));
      }
    } catch (err) {
      if (authRef.current !== sessionToken) return;
      setPendingProgressEvents((prev) =>
        prev.some((e) => e.event_id === eventId) ? prev : [...prev, { event_id: eventId, datos }]
      );
      if (err instanceof APIError) setGlobalError(err);
    }
  };

  return (
    <>
      <a href="#main-content" className="skip-link">
        {t('a11y.ir_al_contenido', idiomaUi)}
      </a>

      <div className="nm-layout">
        {/* SIDEBAR */}
        <aside className="nm-sidebar" aria-label={t('sidebar.titulo', idiomaUi)}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <OraclePillLogo size={36} />
            <div>
              <div style={{ fontWeight: 700, fontSize: '1.15rem', letterSpacing: '-0.02em' }}>
                {t('comun.app_nombre', idiomaUi)}
              </div>
              <div style={{ fontSize: '0.72rem', color: 'var(--oracle-coral)', fontWeight: 600 }}>
                {t('ui.tagline', idiomaUi)}
              </div>
            </div>
          </div>

          {/* Selector de idioma de UI */}
          <div>
            <label className="nm-label" htmlFor="select-ui-lang">
              {t('sidebar.idioma_ui', idiomaUi)}
            </label>
            <select
              id="select-ui-lang"
              className="nm-select"
              value={idiomaUi}
              onChange={(e) => setIdiomaUi(e.target.value as OutputLanguage)}
            >
              {(Object.keys(CATALOGOS) as OutputLanguage[]).map((lang) => (
                <option key={lang} value={lang}>
                  {t(`idioma.${lang}`, idiomaUi)}
                </option>
              ))}
            </select>
          </div>

          <hr style={{ border: 'none', borderTop: '1px solid var(--border-hairline)', width: '100%' }} />

          {/* Panel de Parámetros de Adaptación */}
          {sessionToken && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
              <h3 style={{ margin: '0.25rem 0', fontSize: '0.95rem', color: 'var(--oracle-coral)' }}>
                {t('sidebar.titulo', idiomaUi)}
              </h3>

              {docActivoDemo && (
                <div className="nm-alert nm-alert-warning" style={{ fontSize: '0.8rem', margin: 0 }}>
                  {t('sidebar.demo_generacion_pendiente', idiomaUi)}
                </div>
              )}

              <div>
                <label className="nm-label">{t('sidebar.documento', idiomaUi)}</label>
                {docsReady.length > 0 ? (
                  <select
                    className="nm-select"
                    value={docSeleccionado?.document_id || ''}
                    onChange={(e) => {
                      const newId = e.target.value || null;
                      setDocActivoId(newId);
                      setDocActivoDemo(false);
                      if (newId && docsSeleccionados.length <= 1) {
                        setDocsSeleccionados([newId]);
                      }
                    }}
                  >
                    <option value="" disabled>
                      -- {t('sidebar.documento', idiomaUi)} --
                    </option>
                    {docsReady.map((d) => (
                      <option key={d.document_id} value={d.document_id}>
                        {d.titulo}
                      </option>
                    ))}
                  </select>
                ) : (
                  <div style={{ fontSize: '0.8rem', color: 'var(--ink-muted)' }}>
                    {t('sidebar.sin_documentos', idiomaUi)}
                  </div>
                )}
              </div>

              {docsReady.length > 1 && (
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.35rem' }}>
                    <label className="nm-label" style={{ marginBottom: 0 }}>
                      {t('sidebar.selector_multidocumental', idiomaUi, { n: docsSeleccionados.length })}
                    </label>
                    <div style={{ display: 'flex', gap: '0.4rem', fontSize: '0.72rem' }}>
                      <button
                        type="button"
                        onClick={seleccionarTodosDocs}
                        style={{ padding: 0, fontSize: '0.72rem', color: 'var(--oracle-coral, #c74634)', cursor: 'pointer', background: 'none', border: 'none', textDecoration: 'underline' }}
                      >
                        {t('sidebar.seleccionar_todos', idiomaUi)}
                      </button>
                      <span style={{ color: 'var(--ink-muted)' }}>·</span>
                      <button
                        type="button"
                        onClick={limpiarSeleccionDocs}
                        style={{ padding: 0, fontSize: '0.72rem', color: 'var(--ink-muted)', cursor: 'pointer', background: 'none', border: 'none', textDecoration: 'underline' }}
                      >
                        {t('sidebar.limpiar_seleccion', idiomaUi)}
                      </button>
                    </div>
                  </div>

                  <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem', maxHeight: '130px', overflowY: 'auto', padding: '0.35rem 0.5rem', background: 'var(--surface-sunken)', borderRadius: '4px', border: '1px solid var(--border-hairline)' }}>
                    {docsReady.map((d) => {
                      const checked = docsSeleccionados.includes(d.document_id);
                      return (
                        <label
                          key={d.document_id}
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: '0.5rem',
                            fontSize: '0.8rem',
                            cursor: 'pointer',
                            color: checked ? 'var(--ink)' : 'var(--ink-muted)',
                          }}
                          title={d.titulo}
                        >
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => {
                              setDocActivoDemo(false);
                              toggleDocSeleccionado(d.document_id);
                            }}
                          />
                          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.titulo}</span>
                        </label>
                      );
                    })}
                  </div>
                  {docsSeleccionados.length >= 5 && (
                    <div style={{ fontSize: '0.72rem', color: 'var(--accent-warning, #d97706)', marginTop: '0.25rem' }}>
                      {t('sidebar.limite_fuentes', idiomaUi)}
                    </div>
                  )}
                </div>
              )}

              <div>
                <label className="nm-label" htmlFor="app-field-1">{t('sidebar.perfil', idiomaUi)}</label>
                <select id="app-field-1"
                  className="nm-select"
                  value={genPerfil}
                  onChange={(e) => setGenPerfil(e.target.value as RecipientProfile)}
                >
                  {PERFILES.map((p) => (
                    <option key={p} value={p}>{t(`perfil.${p}`, idiomaUi)}</option>
                  ))}
                </select>
              </div>

              <div>
                <label className="nm-label" htmlFor="app-field-2">{t('sidebar.formato', idiomaUi)}</label>
                <select id="app-field-2"
                  className="nm-select"
                  value={genFormato}
                  onChange={(e) => setGenFormato(e.target.value as PedagogicalFormat)}
                >
                  {FORMATOS.map((f) => (
                    <option key={f} value={f}>{t(`formato.${f}`, idiomaUi)}</option>
                  ))}
                </select>
              </div>

              <div>
                <label className="nm-label" htmlFor="app-field-3">{t('sidebar.nicho', idiomaUi)}</label>
                <select id="app-field-3"
                  className="nm-select"
                  value={genNicho}
                  onChange={(e) => setGenNicho(e.target.value as IndustryNiche)}
                >
                  {NICHOS.map((n) => (
                    <option key={n} value={n}>{t(`nicho.${n}`, idiomaUi)}</option>
                  ))}
                </select>
              </div>

              <div>
                <label className="nm-label" htmlFor="app-field-4">{t('sidebar.detalle', idiomaUi)}</label>
                <select id="app-field-4"
                  className="nm-select"
                  value={genDetalle}
                  onChange={(e) => setGenDetalle(e.target.value as DetailLevel)}
                >
                  {NIVELES_DETALLE.map((d) => (
                    <option key={d} value={d}>{t(`detalle.${d}`, idiomaUi)}</option>
                  ))}
                </select>
              </div>

              <div>
                <label className="nm-label" htmlFor="app-field-5">{t('sidebar.idioma_salida', idiomaUi)}</label>
                <select id="app-field-5"
                  className="nm-select"
                  value={genIdiomaSalida}
                  onChange={(e) => setGenIdiomaSalida(e.target.value as OutputLanguage)}
                >
                  {IDIOMAS_SALIDA.map((l) => (
                    <option key={l} value={l}>{t(`idioma.${l}`, idiomaUi)}</option>
                  ))}
                </select>
              </div>

              <div>
                <label className="nm-label" htmlFor="app-generation-mode">{t('modo.selector', idiomaUi)}</label>
                <select id="app-generation-mode" className="nm-select" value={genModo}
                  onChange={(e) => setGenModo(e.target.value as 'multiagente' | 'basico')}
                  aria-describedby="app-generation-mode-help">
                  <option value="multiagente">{t('modo.multiagente', idiomaUi)}</option>
                  <option value="basico">{t('modo.basico', idiomaUi)}</option>
                </select>
                <p id="app-generation-mode-help" style={{ fontSize: '0.78rem', color: 'var(--ink-muted)' }}>
                  {t(genModo === 'basico' ? 'modo.aviso' : 'modo.ayuda', idiomaUi)}
                </p>
              </div>

              {/* Alcance */}
              <div>
                <label className="nm-label">{t('sidebar.alcance', idiomaUi)}</label>
                {docsSeleccionados.length > 1 ? (
                  <div style={{ fontSize: '0.82rem', padding: '0.4rem 0.6rem', background: 'var(--surface-sunken)', borderRadius: '4px', border: '1px solid var(--border-hairline)' }}>
                    <div style={{ fontWeight: 500, color: 'var(--oracle-coral, #c74634)' }}>
                      {t('sidebar.alcance_multi', idiomaUi, { n: docsSeleccionados.length })}
                    </div>
                    <div style={{ fontSize: '0.74rem', color: 'var(--ink-muted)', marginTop: '0.2rem' }}>
                      {t('sidebar.seccion_deshabilitada_multi', idiomaUi)}
                    </div>
                  </div>
                ) : (
                  <>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem', fontSize: '0.85rem' }}>
                      <label style={{ display: 'flex', alignItems: 'center', gap: '0.45rem' }}>
                        <input
                          type="radio"
                          checked={genAlcance === 'documento_completo'}
                          onChange={() => setGenAlcance('documento_completo')}
                        />
                        {t('sidebar.alcance_completo', idiomaUi)}
                      </label>
                      {seccionesDisponibles.length > 0 && (
                        <label style={{ display: 'flex', alignItems: 'center', gap: '0.45rem' }}>
                          <input
                            type="radio"
                            checked={genAlcance === 'seccion'}
                            onChange={() => {
                              setGenAlcance('seccion');
                              if (!genSeccionId && seccionesDisponibles[0]) {
                                setGenSeccionId(seccionesDisponibles[0].seccion_id);
                              }
                            }}
                          />
                          {t('sidebar.alcance_seccion', idiomaUi)}
                        </label>
                      )}
                    </div>
                    {docSeleccionado && seccionesDisponibles.length === 0 && (
                      <div style={{ fontSize: '0.76rem', color: 'var(--ink-muted)', marginTop: '0.25rem' }}>
                        {t('sidebar.sin_secciones', idiomaUi)}
                      </div>
                    )}
                  </>
                )}
              </div>

              {docsSeleccionados.length <= 1 && genAlcance === 'seccion' && seccionesDisponibles.length > 0 && (
                <div>
                  <label className="nm-label" htmlFor="app-field-6">{t('sidebar.seccion', idiomaUi)}</label>
                  <select id="app-field-6"
                    className="nm-select"
                    value={genSeccionId}
                    onChange={(e) => setGenSeccionId(e.target.value)}
                  >
                    {seccionesDisponibles.map((s) => (
                      <option key={s.seccion_id} value={s.seccion_id}>
                        {t('sidebar.seccion_lineas', idiomaUi, {
                          titulo: s.titulo || s.seccion_id,
                          inicio: s.linea_inicio,
                          fin: s.linea_fin,
                        })}
                      </option>
                    ))}
                  </select>
                </div>
              )}

              <button
                type="button"
                className="nm-btn nm-btn-primary nm-btn-block"
                disabled={docsSeleccionados.length === 0 || (docsSeleccionados.length === 1 && !docSeleccionado) || (docsSeleccionados.length === 1 && genAlcance === 'seccion' && !genSeccionId)}
                onClick={prepararConfirmacionGenerar}
              >
                <Sparkles size={16} />
                {t('sidebar.generar', idiomaUi)}
              </button>
            </div>
          )}

          {/* Presupuesto y Acciones de Sesión */}
          {sessionToken && wsStatus && (
            <div style={{ marginTop: 'auto', paddingTop: '0.85rem', borderTop: '1px solid var(--border-hairline)', fontSize: '0.78rem', color: 'var(--ink-muted)', display: 'flex', flexDirection: 'column', gap: '0.45rem' }}>
              <div>{t('presupuesto.vencimiento', idiomaUi, { fecha: wsStatus.expira_en?.slice(0, 10) })}</div>
              {wsStatus.uso && (
                <div>
                  {t('presupuesto.recursos', idiomaUi, {
                    documentos: wsStatus.uso.documentos,
                    limite_documentos: wsStatus.uso.limite_documentos,
                    generaciones: wsStatus.uso.generaciones,
                    limite_generaciones: wsStatus.uso.limite_generaciones,
                  })}
                </div>
              )}
              {wsStatus.almacenamiento?.escrituras_bloqueadas ? (
                <div className="nm-alert nm-alert-error" style={{ margin: 0, padding: '0.5rem' }}>
                  {t('presupuesto.bloqueado', idiomaUi)}
                </div>
              ) : (
                wsStatus.almacenamiento?.alerta && (
                  <div className="nm-alert nm-alert-warning" style={{ margin: 0, padding: '0.5rem' }}>
                    {t('presupuesto.alerta', idiomaUi)}
                  </div>
                )
              )}

              <button
                type="button"
                className="nm-btn nm-btn-block"
                onClick={() => {
                  setHistorialAbierto(true);
                  setEjemploDemoId(null);
                }}
              >
                <FolderOpen size={14} />
                {t('historial.abrir_vista', idiomaUi)}
              </button>
              <RecentGenerations api={api} token={sessionToken} idioma={idiomaUi} onOpen={() => setHistorialAbierto(true)} />

              <button
                type="button"
                className="nm-btn nm-btn-block"
                onClick={async () => {
                  try {
                    await api.closeSession(sessionToken);
                    limpiarSesionLocal();
                    setMostrandoRecuperacion(true);
                  } catch (err) {
                    if (err instanceof APIError) setGlobalError(err);
                  }
                }}
              >
                <LogOut size={14} />
                {t('sidebar.cerrar_sesion', idiomaUi)}
              </button>

              <div style={{ display: 'flex', gap: '0.4rem' }}>
                <button
                  type="button"
                  className="nm-btn"
                  style={{ flex: 1, fontSize: '0.75rem', padding: '0.4rem' }}
                  onClick={() => setAccionConfirmar('rotar')}
                >
                  <KeyRound size={13} />
                  {t('onboarding.rotar_codigo', idiomaUi)}
                </button>
                <button
                  type="button"
                  className="nm-btn nm-btn-danger"
                  style={{ flex: 1, fontSize: '0.75rem', padding: '0.4rem' }}
                  onClick={() => setAccionConfirmar('borrar')}
                >
                  <Trash2 size={13} />
                  {t('onboarding.borrar_espacio', idiomaUi)}
                </button>
              </div>
            </div>
          )}
        </aside>

        {/* ÁREA PRINCIPAL */}
        <main id="main-content" className="nm-main">
          {avisoSesion && (
            <div className="nm-alert nm-alert-success">
              {t(avisoSesion, idiomaUi)}
            </div>
          )}

          <ErrorBanner
            error={globalError}
            idioma={idiomaUi}
            onDismiss={() => setGlobalError(null)}
          />

          {/* Estado de fallo inicial al crear espacio */}
          {initError && !sessionToken && !mostrandoRecuperacion ? (
            <div className="nm-card">
              <h2>{t('onboarding.bienvenida_titulo', idiomaUi)}</h2>
              <ErrorBanner error={initError} idioma={idiomaUi} />
              <button type="button" className="nm-btn nm-btn-primary" onClick={inicializarEspacio}>
                {t('comun.reintentar', idiomaUi)}
              </button>
            </div>
          ) : (
            <>
              {/* Banner de código de recuperación (emisión única) */}
              {recoveryCode && !mostrandoRecuperacion && (
                <div className="nm-card nm-card-terracotta" style={{ marginBottom: '1.25rem' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--oracle-amber)', fontWeight: 700 }}>
                    <ShieldAlert size={18} />
                    {t('onboarding.codigo_titulo', idiomaUi)}: {t('onboarding.codigo_advertencia', idiomaUi)}
                  </div>
                  <div style={{ fontSize: '0.85rem', color: 'var(--ink-secondary)', marginTop: '0.25rem' }}>
                    {t('onboarding.codigo_descripcion', idiomaUi)}
                  </div>
                  <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center', flexWrap: 'wrap', marginTop: '0.75rem' }}>
                    <code
                      className="mono"
                      style={{
                        flex: 1,
                        padding: '0.65rem 0.9rem',
                        background: '#080a0e',
                        border: '1px solid var(--oracle-coral)',
                        borderRadius: '6px',
                        fontSize: '0.95rem',
                        color: '#fff',
                      }}
                    >
                      {recoveryCode}
                    </code>
                    <button
                      type="button"
                      className="nm-btn"
                      onClick={() => {
                        const blob = new Blob(
                          [`${t('onboarding.codigo_titulo', idiomaUi)}:\n${recoveryCode}\nWorkspace ID: ${workspaceId}`],
                          { type: 'text/plain;charset=utf-8' }
                        );
                        const url = URL.createObjectURL(blob);
                        const a = document.createElement('a');
                        a.href = url;
                        a.download = 'nuevamente_codigo_recuperacion.txt';
                        a.click();
                        URL.revokeObjectURL(url);
                      }}
                    >
                      <Download size={15} />
                      {t('onboarding.descargar_nota', idiomaUi)}
                    </button>
                    <button
                      type="button"
                      className="nm-btn nm-btn-primary"
                      onClick={() => setRecoveryCode(null)}
                    >
                      {t('onboarding.codigo_guardado', idiomaUi)}
                    </button>
                  </div>
                </div>
              )}

              {/* Vistas Principales */}
              {mostrandoRecuperacion ? (
                <RecoveryScreen
                  api={api}
                  idioma={idiomaUi}
                  errorSesionInvalida={errorSesionInvalida}
                  onRecovered={(wsId, tok) => {
                    limpiarSesionLocal();
                    setWorkspaceId(wsId);
                    setSessionToken(tok);
                    setMostrandoRecuperacion(false);
                    setErrorSesionInvalida(false);
                    setAvisoSesion('onboarding.recuperado');
                  }}
                  onBack={() => {
                    setMostrandoRecuperacion(false);
                    setErrorSesionInvalida(false);
                  }}
                />
              ) : historialAbierto && sessionToken && workspaceId ? (
                <HistoryFullView
                  apiClient={api}
                  token={sessionToken}
                  workspaceId={workspaceId}
                  idiomaUi={idiomaUi}
                  onClose={() => setHistorialAbierto(false)}
                  onSessionInvalid={manejarSesionInvalida}
                />
              ) : ejemploDemoId && sessionToken ? (
                <DemoExampleDetailView
                  api={api}
                  token={sessionToken}
                  ejemploId={ejemploDemoId}
                  idioma={idiomaUi}
                  onBack={() => {
                    setEjemploDemoId(null);
                    setVistaOnboarding('demo');
                    setMostrandoDocumentos(true);
                  }}
                  onSessionInvalid={manejarSesionInvalida}
                />
              ) : generacionActiva && !mostrandoDocumentos && sessionToken && workspaceId ? (
                <div>
                  {/* Cabecera con parámetros fijos de la generación */}
                  <div className="nm-card" style={{ marginBottom: '1rem' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.5rem' }}>
                      <div>
                        <h2 style={{ margin: 0, fontSize: '1.2rem' }}>{t('generar.cabecera_titulo', idiomaUi)}</h2>
                        <div style={{ fontSize: '0.84rem', color: 'var(--ink-secondary)', marginTop: '0.25rem' }}>
                          <strong>{generacionActiva.documento_titulo}</strong> ·{' '}
                          {t(`perfil.${generacionActiva.parametros.perfil_destinatario}`, idiomaUi)} ·{' '}
                          {t(`formato.${generacionActiva.parametros.formato_salida}`, idiomaUi)} ·{' '}
                          {t(`nicho.${generacionActiva.parametros.nicho_sector}`, idiomaUi)} ·{' '}
                          {t(`idioma.${generacionActiva.parametros.idioma_salida}`, idiomaUi)}
                          {' · '}{t(`modo.${generacionActiva.parametros.modo_generacion ?? 'multiagente'}`, idiomaUi)}
                        </div>
                      </div>
                      <button
                        type="button"
                        className="nm-btn"
                        onClick={() => setMostrandoDocumentos(true)}
                      >
                        <FileText size={15} />
                        {t('nav.documentos', idiomaUi)}
                      </button>
                    </div>
                  </div>

                  {/* Pestañas: Material / Tutor / Glosario / Progreso */}
                  <div className="nm-tabs" role="tablist">
                    {(
                      [
                        ['resultado', t('nav.resultado', idiomaUi)],
                        ['tutor', t('chat.titulo', idiomaUi)],
                        ['glosario', t('nav.glosario', idiomaUi)],
                        ['progreso', t('nav.progreso', idiomaUi)],
                      ] as const
                    ).map(([tabKey, label]) => (
                      <button
                        key={tabKey}
                        type="button"
                        role="tab"
                        aria-selected={activeTab === tabKey}
                        className={`nm-tab ${activeTab === tabKey ? 'nm-tab-active' : ''}`}
                        onClick={() => setActiveTab(tabKey)}
                      >
                        {label}
                      </button>
                    ))}
                  </div>

                  {activeTab === 'resultado' && (
                    <ActiveGenerationPanel
                      key={`${sessionToken}:${generacionActiva.generation_id}`}
                      api={api}
                      token={sessionToken}
                      generacion={generacionActiva}
                      job={activeJobData}
                      idioma={idiomaUi}
                      stream={generationStream}
                      onChangeSource={() => setMostrandoDocumentos(true)}
                      onNarrowScope={() => {
                        setGenAlcance('seccion');
                        if (seccionesDisponibles[0]) setGenSeccionId(seccionesDisponibles[0].seccion_id);
                        requestAnimationFrame(() => document.getElementById('app-field-6')?.focus());
                      }}
                      confirmedConcepts={confirmedConcepts}
                      onRefresh={consultarGeneracionActiva}
                      onMarkConcept={(docId, concepto, genId) =>
                        enviarEventoProgreso(`ui41-concept-${docId}-${concepto}`, {
                          tipo: 'concepto_revisado',
                          document_id: docId,
                          concepto,
                          generation_id: genId,
                        })
                      }
                      onReviewFlashcard={(genId, fcId) =>
                        enviarEventoProgreso(`ui41-fc-${genId}-${fcId}`, {
                          tipo: 'flashcard_vista',
                          generation_id: genId,
                          flashcard_id: fcId,
                        })
                      }
                      onSessionInvalid={manejarSesionInvalida}
                    />
                  )}

                  {activeTab === 'tutor' && (
                    <ChatTutorView
                      apiClient={api}
                      token={sessionToken}
                      workspaceId={workspaceId}
                      documentId={generacionActiva.parametros.document_id || (generacionActiva.parametros.document_ids?.[0]) || ''}
                      documentIds={generacionActiva.parametros.document_ids}
                      perfil={generacionActiva.parametros.perfil_destinatario}
                      idiomaSalida={generacionActiva.parametros.idioma_salida}
                      idiomaUi={idiomaUi}
                      onSessionInvalid={manejarSesionInvalida}
                    />
                  )}

                  {activeTab === 'glosario' && (
                    <GlossaryView
                      apiClient={api}
                      token={sessionToken}
                      documentId={generacionActiva.parametros.document_id || (generacionActiva.parametros.document_ids?.[0]) || ''}
                      documentIds={generacionActiva.parametros.document_ids}
                      perfil={generacionActiva.parametros.perfil_destinatario}
                      idiomaSalida={generacionActiva.parametros.idioma_salida}
                      idiomaUi={idiomaUi}
                      onSessionInvalid={manejarSesionInvalida}
                    />
                  )}

                  {activeTab === 'progreso' && (
                    <ProgressView
                      apiClient={api}
                      token={sessionToken}
                      idiomaUi={idiomaUi}
                      pendingEvents={pendingProgressEvents}
                      onRetryPending={async (evId) => {
                        const ev = pendingProgressEvents.find((x) => x.event_id === evId);
                        if (ev) await enviarEventoProgreso(ev.event_id, ev.datos);
                      }}
                      onSessionInvalid={manejarSesionInvalida}
                    />
                  )}
                </div>
              ) : (
                <div>
                  {generacionActiva && (
                    <button
                      type="button"
                      className="nm-btn nm-btn-primary"
                      style={{ marginBottom: '1rem' }}
                      onClick={() => setMostrandoDocumentos(false)}
                    >
                      {t('generar.volver_resultado', idiomaUi)}
                    </button>
                  )}

                  {/* Hero Banner Oracle Redwood */}
                  <div className="nm-hero">
                    <OracleWaveCircuitBackdrop />
                    <div className="nm-hero-content">
                      <span className="nm-badge" style={{ marginBottom: '0.6rem' }}>
                        RAG · LANGGRAPH · OCI OBJECT STORAGE
                      </span>
                      <h1 style={{ margin: '0 0 0.4rem 0', fontSize: '1.85rem' }}>
                        {t('onboarding.bienvenida_titulo', idiomaUi)}
                      </h1>
                      <p style={{ margin: 0, color: 'var(--ink-secondary)', maxWidth: '640px' }}>
                        {t('onboarding.bienvenida_descripcion', idiomaUi)}
                      </p>
                    </div>
                  </div>

                  {/* 4 Acciones de Onboarding */}
                  <div className="nm-grid-4" style={{ marginBottom: '1.25rem' }}>
                    <button
                      type="button"
                      className={`nm-btn ${vistaOnboarding === 'upload' ? 'nm-btn-primary' : ''}`}
                      onClick={() => setVistaOnboarding('upload')}
                    >
                      <Upload size={16} />
                      {t('upload.titulo', idiomaUi)}
                    </button>
                    <button
                      type="button"
                      className={`nm-btn ${vistaOnboarding === 'texto' ? 'nm-btn-primary' : ''}`}
                      onClick={() => setVistaOnboarding('texto')}
                    >
                      <FileText size={16} />
                      {t('upload.enviar_texto', idiomaUi)}
                    </button>
                    <button
                      type="button"
                      className={`nm-btn ${vistaOnboarding === 'demo' ? 'nm-btn-primary' : ''}`}
                      onClick={() => setVistaOnboarding('demo')}
                    >
                      <Layers size={16} />
                      {t('onboarding.demo', idiomaUi)}
                    </button>
                    <button
                      type="button"
                      className="nm-btn"
                      onClick={() => setMostrandoRecuperacion(true)}
                    >
                      <KeyRound size={16} />
                      {t('onboarding.recuperar_titulo', idiomaUi)}
                    </button>
                  </div>

                  {vistaOnboarding === 'upload' && sessionToken && (
                    <FileUpload
                      api={api}
                      token={sessionToken}
                      idioma={idiomaUi}
                      onUploaded={(docId, jobId) => {
                        setTrabajosDoc((prev) => ({ ...prev, [docId]: { jobId, cursor: 0, estado: 'queued' } }));
                        sincronizarEspacio();
                      }}
                      onError={setGlobalError}
                    />
                  )}

                  {vistaOnboarding === 'texto' && sessionToken && (
                    <PasteTextUpload
                      api={api}
                      token={sessionToken}
                      idioma={idiomaUi}
                      onUploaded={(docId, jobId) => {
                        setTrabajosDoc((prev) => ({ ...prev, [docId]: { jobId, cursor: 0, estado: 'queued' } }));
                        sincronizarEspacio();
                      }}
                      onError={setGlobalError}
                    />
                  )}

                  {vistaOnboarding === 'demo' && sessionToken && (
                    <DemoCatalogSection
                      api={api}
                      token={sessionToken}
                      idioma={idiomaUi}
                      docActivoId={docActivoId}
                      docActivoDemo={docActivoDemo}
                      onSelectDemoDoc={(id) => {
                        setDocActivoId(id);
                        setDocActivoDemo(true);
                      }}
                      onOpenDemoExample={(gid) => {
                        setEjemploDemoId(gid);
                        setHistorialAbierto(false);
                      }}
                      onError={setGlobalError}
                    />
                  )}

                  {/* Lista de Documentos del Espacio */}
                  <div className="nm-card" style={{ marginTop: '1.25rem' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.85rem' }}>
                      <h3 style={{ margin: 0 }}>{t('nav.documentos', idiomaUi)}</h3>
                      <button type="button" className="nm-btn" onClick={sincronizarEspacio}>
                        <RefreshCw size={14} />
                        {t('comun.reintentar', idiomaUi)}
                      </button>
                    </div>

                    {avisoDocBorrado && (
                      <div className="nm-alert nm-alert-success">{t('upload.borrado_recibido', idiomaUi)}</div>
                    )}

                    {documentos.length === 0 ? (
                      <div className="nm-alert nm-alert-info">{t('upload.sin_documentos', idiomaUi)}</div>
                    ) : (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                        {documentos.map((doc) => {
                          const activo = docActivoId === doc.document_id && !docActivoDemo;
                          const provider = wsStatus?.almacenamiento?.provider;
                          return (
                            <div
                              key={doc.document_id}
                              className="nm-card"
                              style={{
                                background: 'var(--surface-2)',
                                borderColor: activo ? 'var(--oracle-coral)' : 'var(--border-hairline)',
                              }}
                            >
                              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '0.75rem' }}>
                                <div>
                                  <div style={{ fontWeight: 600, fontSize: '1rem' }}>{doc.titulo}</div>
                                  <DocumentStatus doc={doc} tracking={trabajosDoc[doc.document_id]} idioma={idiomaUi} />
                                  <div style={{ fontSize: '0.8rem', color: 'var(--ink-muted)', display: 'flex', gap: '0.6rem', flexWrap: 'wrap', marginTop: '0.25rem' }}>
                                    <span className="nm-badge nm-badge-neutral">
                                      {t(`estados.documento.${doc.status}`, idiomaUi)}
                                    </span>
                                    {doc.metadata && (
                                      <span>
                                        {doc.metadata.extension.toUpperCase()} · {formatearTamano(doc.metadata.tamano_bytes)}
                                      </span>
                                    )}
                                    {provider && (
                                      <span>
                                        {t(provider === 'mock' ? 'almacenamiento.local_dev' : 'upload.guardado_oci', idiomaUi)}
                                      </span>
                                    )}
                                    {doc.cobertura && doc.metadata && (
                                      <span>
                                        {doc.metadata.extension !== 'pdf'
                                          ? t('upload.cobertura_texto', idiomaUi, {
                                              secciones: doc.cobertura.secciones_procesadas?.length || 0,
                                              tokens: doc.metadata.cantidad_tokens ?? '—',
                                            })
                                          : t('upload.cobertura', idiomaUi, {
                                              procesadas: doc.cobertura.paginas_procesadas?.length || 0,
                                              total: doc.metadata.cantidad_paginas ?? '—',
                                            })}
                                      </span>
                                    )}
                                  </div>
                                </div>

                                <div style={{ display: 'flex', gap: '0.5rem' }}>
                                  <button
                                    type="button"
                                    className={`nm-btn ${activo ? 'nm-btn-primary' : ''}`}
                                    disabled={doc.status !== 'ready' || activo}
                                    onClick={() => {
                                      setDocActivoId(doc.document_id);
                                      setDocActivoDemo(false);
                                    }}
                                  >
                                    {t(activo ? 'upload.activo' : 'upload.seleccionar_documento', idiomaUi)}
                                  </button>
                                  <button
                                    type="button"
                                    className="nm-btn nm-btn-danger"
                                    onClick={() => setConfirmDeleteDocId(doc.document_id)}
                                  >
                                    {t('upload.eliminar', idiomaUi)}
                                  </button>
                                </div>
                              </div>

                              {confirmDeleteDocId === doc.document_id && sessionToken && (
                                <div className="nm-alert nm-alert-warning" style={{ marginTop: '0.75rem', marginBottom: 0 }}>
                                  <div>{t('upload.borrar_confirmacion', idiomaUi)}</div>
                                  <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.5rem' }}>
                                    <button
                                      type="button"
                                      className="nm-btn nm-btn-danger"
                                      onClick={async () => {
                                        try {
                                          await api.deleteDocument(sessionToken, doc.document_id);
                                          setConfirmDeleteDocId(null);
                                          setAvisoDocBorrado(true);
                                          sincronizarEspacio();
                                        } catch (err) {
                                          if (err instanceof APIError) setGlobalError(err);
                                        }
                                      }}
                                    >
                                      {t('comun.confirmar', idiomaUi)}
                                    </button>
                                    <button
                                      type="button"
                                      className="nm-btn"
                                      onClick={() => setConfirmDeleteDocId(null)}
                                    >
                                      {t('comun.cancelar', idiomaUi)}
                                    </button>
                                  </div>
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>

                  {/* Herramientas de Estudio en Home */}
                  {sessionToken && workspaceId && (
                    <div style={{ marginTop: '1.25rem' }}>
                      {docSeleccionado ? (
                        <div className="nm-card">
                          <h3 style={{ marginTop: 0 }}>{t('estudio.herramientas', idiomaUi)}</h3>
                          <div className="nm-grid-2">
                            <ChatTutorView
                              apiClient={api}
                              token={sessionToken}
                              workspaceId={workspaceId}
                              documentId={docSeleccionado.document_id}
                              documentIds={docsSeleccionados.length > 1 ? docsSeleccionados : undefined}
                              perfil={genPerfil}
                              idiomaSalida={genIdiomaSalida}
                              idiomaUi={idiomaUi}
                              onSessionInvalid={manejarSesionInvalida}
                            />
                            <GlossaryView
                              apiClient={api}
                              token={sessionToken}
                              documentId={docSeleccionado.document_id}
                              documentIds={docsSeleccionados.length > 1 ? docsSeleccionados : undefined}
                              perfil={genPerfil}
                              idiomaSalida={genIdiomaSalida}
                              idiomaUi={idiomaUi}
                              onSessionInvalid={manejarSesionInvalida}
                            />
                          </div>
                          <div style={{ marginTop: '1rem' }}>
                            <ProgressView
                              apiClient={api}
                              token={sessionToken}
                              idiomaUi={idiomaUi}
                              pendingEvents={pendingProgressEvents}
                              onRetryPending={async (evId) => {
                                const ev = pendingProgressEvents.find((x) => x.event_id === evId);
                                if (ev) await enviarEventoProgreso(ev.event_id, ev.datos);
                              }}
                              onSessionInvalid={manejarSesionInvalida}
                            />
                          </div>
                        </div>
                      ) : (
                        <ProgressView
                          apiClient={api}
                          token={sessionToken}
                          idiomaUi={idiomaUi}
                          pendingEvents={pendingProgressEvents}
                          onRetryPending={async (evId) => {
                            const ev = pendingProgressEvents.find((x) => x.event_id === evId);
                            if (ev) await enviarEventoProgreso(ev.event_id, ev.datos);
                          }}
                          onSessionInvalid={manejarSesionInvalida}
                        />
                      )}
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </main>
      </div>

      {/* Modal de Confirmación de Generación */}
      {confirmModalPending && (
        <div className="nm-modal-backdrop" role="dialog" aria-modal="true" aria-label={t('generar.resumen_titulo', idiomaUi)}>
          <div className="nm-modal">
            <h3 style={{ marginTop: 0 }}>{t('generar.resumen_titulo', idiomaUi)}</h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.45rem', marginBottom: '1.25rem', fontSize: '0.9rem' }}>
              <div><strong>{t('sidebar.documento', idiomaUi)}:</strong> {confirmModalPending.documentoTitulo}</div>
              <div><strong>{t('sidebar.perfil', idiomaUi)}:</strong> {t(`perfil.${confirmModalPending.peticion.perfil_destinatario}`, idiomaUi)}</div>
              <div><strong>{t('sidebar.formato', idiomaUi)}:</strong> {t(`formato.${confirmModalPending.peticion.formato_salida}`, idiomaUi)}</div>
              <div><strong>{t('sidebar.nicho', idiomaUi)}:</strong> {t(`nicho.${confirmModalPending.peticion.nicho_sector}`, idiomaUi)}</div>
              <div><strong>{t('sidebar.detalle', idiomaUi)}:</strong> {t(`detalle.${confirmModalPending.peticion.nivel_detalle}`, idiomaUi)}</div>
              <div><strong>{t('modo.selector', idiomaUi)}:</strong> {t(`modo.${confirmModalPending.peticion.modo_generacion ?? 'multiagente'}`, idiomaUi)}</div>
              {confirmModalPending.peticion.modo_generacion === 'basico' && <p className="nm-alert nm-alert-warning">{t('modo.aviso', idiomaUi)}</p>}
              <div><strong>{t('sidebar.idioma_salida', idiomaUi)}:</strong> {t(`idioma.${confirmModalPending.peticion.idioma_salida}`, idiomaUi)}</div>
              <div>
                <strong>{t('sidebar.alcance', idiomaUi)}:</strong>{' '}
                {confirmModalPending.peticion.alcance.tipo === 'seccion'
                  ? `${t('sidebar.alcance_seccion', idiomaUi)}: ${confirmModalPending.seccionTitulo || confirmModalPending.peticion.alcance.seccion_id}`
                  : confirmModalPending.peticion.alcance.tipo === 'multi_documento'
                  ? t('sidebar.alcance_multi', idiomaUi, { n: confirmModalPending.peticion.document_ids?.length || 2 })
                  : t('sidebar.alcance_completo', idiomaUi)}
              </div>
            </div>
            <div className="nm-grid-2">
              <button type="button" className="nm-btn nm-btn-primary" onClick={confirmarYEnviarGeneracion}>
                {t('generar.confirmar', idiomaUi)}
              </button>
              <button type="button" className="nm-btn" onClick={() => setConfirmModalPending(null)}>
                {t('comun.atras', idiomaUi)}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Modal de Rotación / Borrado de Espacio */}
      {accionConfirmar && sessionToken && (
        <div className="nm-modal-backdrop" role="dialog" aria-modal="true">
          <div className="nm-modal">
            <h3 style={{ marginTop: 0 }}>
              {t(accionConfirmar === 'rotar' ? 'onboarding.rotar_codigo' : 'onboarding.borrar_espacio', idiomaUi)}
            </h3>
            <div className="nm-alert nm-alert-warning">
              {t(
                accionConfirmar === 'rotar'
                  ? 'onboarding.rotar_confirmacion'
                  : 'onboarding.borrar_confirmacion',
                idiomaUi
              )}
            </div>
            <div className="nm-grid-2">
              <button
                type="button"
                className="nm-btn nm-btn-primary"
                onClick={async () => {
                  try {
                    if (accionConfirmar === 'rotar') {
                      const res = await api.rotateRecoveryCode(sessionToken);
                      setSessionToken(res.token);
                      setRecoveryCode(res.recovery_code);
                    } else {
                      await api.deleteWorkspace(sessionToken);
                      limpiarSesionLocal();
                      setAvisoSesion('onboarding.borrado_recibido');
                    }
                    setAccionConfirmar(null);
                  } catch (err) {
                    if (err instanceof APIError) setGlobalError(err);
                  }
                }}
              >
                {t('comun.si', idiomaUi)}
              </button>
              <button type="button" className="nm-btn" onClick={() => setAccionConfirmar(null)}>
                {t('comun.no', idiomaUi)}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
};

// Subcomponentes auxiliares
const FileUpload: React.FC<{
  api: APIClient;
  token: string;
  idioma: OutputLanguage;
  onUploaded: (docId: string, jobId: string) => void;
  onError: (err: APIError) => void;
}> = ({ api, token, idioma, onUploaded, onError }) => {
  const [file, setFile] = useState<File | null>(null);
  const [permiso, setPermiso] = useState(false);
  const [busy, setBusy] = useState(false);

  const [valido, errClave] = file ? validarArchivo(file.name, file.size) : [false, null];

  const subir = async () => {
    if (!file || !valido || !permiso || busy) return;
    setBusy(true);
    try {
      const ext = file.name.toLowerCase().slice(file.name.lastIndexOf('.'));
      const mime =
        ext === '.pdf' ? 'application/pdf' : ext === '.md' ? 'text/markdown' : 'text/plain';
      const recibo = await api.uploadFile(token, file.name, file, mime, {
        title: file.name.slice(0, 300),
        idempotencyKey: await claveCarga(file.name.slice(0, 300), new Uint8Array(await file.arrayBuffer())),
      });
      setFile(null);
      setPermiso(false);
      onUploaded(recibo.document_id, recibo.job_id);
    } catch (err) {
      if (err instanceof APIError) onError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="nm-card" aria-labelledby="file-upload-title" style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
      <h2 id="file-upload-title" style={{ margin: 0, fontSize: '1.2rem' }}>{t('upload.titulo', idioma)}</h2>
      <div id="file-upload-help" style={{ fontSize: '0.85rem', color: 'var(--ink-muted)' }}>
        {t('upload.formatos_soportados', idioma)} · {t('upload.limite', idioma)}
      </div>
      <p className="nm-alert nm-alert-info">{t('upload.aviso_privacidad', idioma)}</p>
      <input
        aria-labelledby="file-upload-title"
        aria-describedby="file-upload-help"
        type="file"
        accept=".pdf,.md,.txt"
        className="nm-input"
        style={{ fontSize: '0.9rem' }}
        onChange={(e) => setFile(e.target.files?.[0] || null)}
      />
      {errClave && (
        <div className="nm-alert nm-alert-error" style={{ margin: 0, padding: '0.45rem', fontSize: '0.8rem' }}>
          {t(errClave, idioma)}
        </div>
      )}
      <label style={{ display: 'flex', alignItems: 'flex-start', gap: '0.45rem', fontSize: '0.78rem', color: 'var(--ink-secondary)' }}>
        <input
          type="checkbox"
          checked={permiso}
          onChange={(e) => setPermiso(e.target.checked)}
          style={{ marginTop: '0.2rem' }}
        />
        <span>{t('upload.confirmar_publico', idioma)}</span>
      </label>
      <button
        type="button"
        className="nm-btn nm-btn-primary nm-btn-block"
        disabled={!valido || !permiso || busy}
        onClick={subir}
      >
        {t('upload.procesar', idioma)}
      </button>
    </section>
  );
};

const PasteTextUpload: React.FC<{
  api: APIClient;
  token: string;
  idioma: OutputLanguage;
  onUploaded: (docId: string, jobId: string) => void;
  onError: (err: APIError) => void;
}> = ({ api, token, idioma, onUploaded, onError }) => {
  const [titulo, setTitulo] = useState('');
  const [contenido, setContenido] = useState('');
  const [permiso, setPermiso] = useState(false);
  const [localErr, setLocalErr] = useState<string | null>(null);

  const guardar = async () => {
    setLocalErr(null);
    const [ok, errKey] = validarTextoPegado(titulo, contenido);
    if (!ok && errKey) {
      setLocalErr(t(errKey, idioma));
      return;
    }
    try {
      const recibo = await api.uploadDocument(
        token,
        { documento_titulo: titulo.trim(), documento_contenido: contenido },
        await claveCarga(titulo.trim(), contenido)
      );
      setTitulo('');
      setContenido('');
      setPermiso(false);
      onUploaded(recibo.document_id, recibo.job_id);
    } catch (err) {
      if (err instanceof APIError) onError(err);
    }
  };

  return (
    <div className="nm-card">
      <h3 style={{ marginTop: 0 }}>{t('upload.enviar_texto', idioma)}</h3>
      <p className="nm-alert nm-alert-info">{t('upload.aviso_privacidad', idioma)}</p>
      {localErr && <div className="nm-alert nm-alert-error">{localErr}</div>}
      <div style={{ marginBottom: '0.75rem' }}>
        <label className="nm-label" htmlFor="app-field-7">{t('upload.texto_titulo', idioma)}</label>
        <input id="app-field-7"
          type="text"
          className="nm-input"
          maxLength={300}
          value={titulo}
          onChange={(e) => setTitulo(e.target.value)}
        />
      </div>
      <div style={{ marginBottom: '0.75rem' }}>
        <label className="nm-label" htmlFor="app-field-8">{t('upload.texto_contenido', idioma)}</label>
        <textarea id="app-field-8"
          className="nm-textarea"
          rows={6}
          value={contenido}
          onChange={(e) => setContenido(e.target.value)}
        />
      </div>
      <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.85rem', marginBottom: '0.75rem' }}>
        <input type="checkbox" checked={permiso} onChange={(e) => setPermiso(e.target.checked)} />
        {t('upload.confirmar_publico', idioma)}
      </label>
      <button type="button" className="nm-btn nm-btn-primary" disabled={!permiso} onClick={guardar}>
        {t('upload.enviar_texto', idioma)}
      </button>
    </div>
  );
};

const RecoveryScreen: React.FC<{
  api: APIClient;
  idioma: OutputLanguage;
  errorSesionInvalida: boolean;
  onRecovered: (wsId: string, token: string) => void;
  onBack: () => void;
}> = ({ api, idioma, errorSesionInvalida, onRecovered, onBack }) => {
  const [code, setCode] = useState('');
  const [err, setErr] = useState<APIError | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    try {
      const res = await api.recoverSession(code.trim());
      onRecovered(res.workspace_id, res.token);
    } catch (error) {
      if (error instanceof APIError) setErr(error);
    }
  };

  return (
    <div className="nm-card" style={{ maxWidth: '520px', margin: '2rem auto' }}>
      {errorSesionInvalida && (
        <div className="nm-alert nm-alert-warning">{t('errors.SESSION_INVALID', idioma)}</div>
      )}
      <h2 style={{ marginTop: 0 }}>{t('onboarding.recuperar_titulo', idioma)}</h2>
      <p style={{ color: 'var(--ink-secondary)', fontSize: '0.9rem' }}>
        {t('onboarding.recuperar_ayuda', idioma)}
      </p>
      <ErrorBanner error={err} idioma={idioma} />
      <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: '0.85rem' }}>
        <input
          type="password"
          className="nm-input mono"
          placeholder="xxxx-xxxx-xxxx-xxxx-xxxx-xxxx-xxxx-xxxx"
          value={code}
          onChange={(e) => setCode(e.target.value)}
        />
        <div style={{ display: 'flex', gap: '0.6rem' }}>
          <button type="submit" className="nm-btn nm-btn-primary">
            {t('onboarding.recuperar_boton', idioma)}
          </button>
          <button type="button" className="nm-btn" onClick={onBack}>
            {t('comun.atras', idioma)}
          </button>
        </div>
      </form>
    </div>
  );
};

export const ActiveGenerationPanel: React.FC<{
  api: APIClient;
  token: string;
  generacion: GeneracionActivaUI;
  job: any | null;
  idioma: OutputLanguage;
  confirmedConcepts: Record<string, boolean>;
  onRefresh: () => void;
  onMarkConcept: (docId: string, concepto: string, genId: string) => void;
  onReviewFlashcard: (genId: string, fcId: string) => void;
  onSessionInvalid: () => void;
  stream: ReturnType<typeof useGenerationProgress>;
  onChangeSource: () => void;
  onNarrowScope: () => void;
}> = ({
  api,
  token,
  generacion,
  job,
  idioma,
  confirmedConcepts,
  onRefresh,
  onMarkConcept,
  onReviewFlashcard,
  onSessionInvalid,
  stream,
  onChangeSource,
  onNarrowScope,
}) => {
  const [persistKey] = useState(() => `persist-${generacion.generation_id}-${Date.now().toString(36)}`);
  const [err, setErr] = useState<APIError | null>(null);
  const [pendingAction, setPendingAction] = useState<'cancel' | 'persist' | null>(null);
  const [cancelRequested, setCancelRequested] = useState(false);

  if (!job) {
    return <div className="nm-card"><GenerationLiveStatus stream={stream} idioma={idioma} modo={generacion.parametros.modo_generacion} /></div>;
  }

  const estado = job.status;

  if (estado === 'completed') {
    let paqueteValido: PedagogicalStudentOutput;
    try {
      paqueteValido = validarGeneracionCompletada(job, generacion.generation_id, generacion.parametros);
    } catch (e) {
      const errorObj = e instanceof APIError ? e : new APIError('INVALID_RESPONSE', 'Resultado incompatible con la solicitud.', 502);
      return (
        <div>
          <ErrorBanner error={errorObj} idioma={idioma} />
        </div>
      );
    }
    return (
      <div>
        <ErrorBanner error={err} idioma={idioma} />
        <ExportDownloads
          api={api}
          token={token}
          paquete={paqueteValido}
          persistencia={job.persistencia}
          idioma={idioma}
          onError={setErr}
          onSessionInvalid={onSessionInvalid}
        />

        <PackageViewer
          paquete={paqueteValido}
          idioma={idioma}
          apiClient={api}
          token={token}
          confirmedConcepts={confirmedConcepts}
          onMarkConcept={onMarkConcept}
          onReviewFlashcard={onReviewFlashcard}
          onSessionInvalid={onSessionInvalid}
        />
      </div>
    );
  }

  if (estado === 'queued' || estado === 'running') {
    return (
      <div className="nm-card">
        <ErrorBanner error={err} idioma={idioma} />
        <div className="nm-alert nm-alert-info">{t('generar.enviada', idioma)}</div>
        <div style={{ fontWeight: 600, marginBottom: '0.35rem' }}>
          {t(`estados.trabajo.${estado}`, idioma)}
        </div>
        <GenerationLiveStatus stream={stream} idioma={idioma} modo={generacion.parametros.modo_generacion} />
        {cancelRequested && <p role="status">{t('live.cancel_requested', idioma)}</p>}
        {estado === 'queued' && typeof job.posicion_cola === 'number' && (
          <div style={{ fontSize: '0.85rem', color: 'var(--ink-secondary)' }}>
            {t('generar.posicion', idioma, { posicion: job.posicion_cola })}
          </div>
        )}
        <div style={{ fontSize: '0.82rem', color: 'var(--ink-muted)', marginTop: '0.35rem' }}>
          {t('material.en_proceso', idioma)}
        </div>
        <div style={{ display: 'flex', gap: '0.6rem', marginTop: '1rem' }}>
          <button type="button" className="nm-btn" onClick={onRefresh}>
            {t('material.actualizar', idioma)}
          </button>
          <button
            type="button"
            className="nm-btn nm-btn-danger"
            disabled={cancelRequested || pendingAction !== null}
            onClick={async () => {
              setPendingAction('cancel'); setErr(null);
              try {
                await api.cancelGeneration(token, generacion.generation_id);
                setCancelRequested(true);
                await onRefresh();
              } catch (error) {
                if (error instanceof APIError && error.statusCode === 401) onSessionInvalid();
                else if (error instanceof APIError) setErr(error);
              } finally { setPendingAction(null); }
            }}
          >
            {t('generar.cancelar', idioma)}
          </button>
        </div>
      </div>
    );
  }

  if (estado === 'cancelled') {
    return <div className="nm-alert nm-alert-info">{t('material.cancelado', idioma)}</div>;
  }

  if (estado === 'rejected_quality') {
    return (
      <div className="nm-card">
        <div className="nm-alert nm-alert-error">{t('generar.rechazado_calidad', idioma)}</div>
        <p>{t('live.rejected_reason', idioma)}</p>
        <div style={{ fontSize: '0.88rem', color: 'var(--ink-secondary)' }}>
          {t('material.rechazo_ayuda', idioma)}
        </div>
        <div className="nm-actions">
          <button type="button" className="nm-btn" onClick={onNarrowScope}>{t('live.narrow', idioma)}</button>
          <button type="button" className="nm-btn" onClick={onChangeSource}>{t('live.source', idioma)}</button>
        </div>
      </div>
    );
  }

  const cause = job.error?.details?.causa;
  const diagnostic = typeof cause === 'string' && CATALOGOS[idioma][`live.cause.${cause}`]
    ? `live.cause.${cause}` : 'live.cause.UNKNOWN';
  return (
    <div className="nm-card">
      <ErrorBanner error={err} idioma={idioma} />
      <ErrorBanner
        error={new APIError(job.error?.code || 'INTERNAL', 'Trabajo fallido.', 500)}
        idioma={idioma}
      />
      <p>{t(diagnostic, idioma)}</p>
      <button type="button" className="nm-btn" onClick={onRefresh}>{t('material.actualizar', idioma)}</button>
      {job.error?.code === 'STORAGE_UNAVAILABLE' && (
        <div style={{ marginTop: '0.75rem' }}>
          <div style={{ fontSize: '0.84rem', color: 'var(--ink-secondary)', marginBottom: '0.5rem' }}>
            {t('material.persist_ayuda', idioma)}
          </div>
          <button
            type="button"
            className="nm-btn nm-btn-primary"
            disabled={pendingAction !== null}
            onClick={async () => {
              setPendingAction('persist'); setErr(null);
              try {
                await api.persistGeneration(token, generacion.generation_id, persistKey);
                await onRefresh();
              } catch (error) {
                if (error instanceof APIError && error.statusCode === 401) onSessionInvalid();
                else if (error instanceof APIError) setErr(error);
              } finally { setPendingAction(null); }
            }}
          >
            <RotateCcw size={14} />
            {t('material.persist_reintentar', idioma)}
          </button>
        </div>
      )}
    </div>
  );
};

const DemoCatalogSection: React.FC<{
  api: APIClient;
  token: string;
  idioma: OutputLanguage;
  docActivoId: string | null;
  docActivoDemo: boolean;
  onSelectDemoDoc: (id: string) => void;
  onOpenDemoExample: (gid: string) => void;
  onError: (err: APIError) => void;
}> = ({
  api,
  token,
  idioma,
  docActivoId,
  docActivoDemo,
  onSelectDemoDoc,
  onOpenDemoExample,
  onError,
}) => {
  const [catalogoEjemplos, setCatalogoEjemplos] = useState<any | null>(null);
  const [demoDocs, setDemoDocs] = useState<Record<string, DocumentoResponse>>({});

  useEffect(() => {
    api.getDemoGenerations(token).then(setCatalogoEjemplos).catch(() => {});
    api
      .getDemoDocuments(token, { limit: 100 })
      .then((res) => {
        const m: Record<string, DocumentoResponse> = {};
        for (const d of res?.items || []) m[d.document_id] = d;
        setDemoDocs(m);
      })
      .catch((err) => {
        if (err instanceof APIError) onError(err);
      });
  }, [api, token]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
      {/* Ejemplos curados */}
      <div className="nm-card">
        <h3 style={{ marginTop: 0 }}>{t('demo.ejemplos', idioma)}</h3>
        <div style={{ fontSize: '0.85rem', color: 'var(--ink-secondary)', marginBottom: '0.85rem' }}>
          {t('demo.ayuda', idioma)}
        </div>
        {!catalogoEjemplos?.items?.length ? (
          <div className="nm-alert nm-alert-info">{t('demo.vacio', idioma)}</div>
        ) : (
          <div className="nm-grid-2">
            {catalogoEjemplos.items.map((item: any) => (
              <div key={item.generation_id} className="nm-card" style={{ background: 'var(--surface-2)' }}>
                <div style={{ fontWeight: 600 }}>{item.fuente}</div>
                <div style={{ fontSize: '0.8rem', color: 'var(--ink-muted)', margin: '0.35rem 0' }}>
                  {[
                    t(`perfil.${item.parametros.perfil_destinatario}`, idioma),
                    t(`formato.${item.parametros.formato_salida}`, idioma),
                    t(`nicho.${item.parametros.nicho_sector}`, idioma),
                    t(`idioma.${item.parametros.idioma_salida}`, idioma),
                  ].join(' · ')}
                </div>
                <div style={{ fontSize: '0.78rem', color: 'var(--oracle-coral)', marginBottom: '0.6rem' }}>
                  {t('historial.ejemplo_guardado', idioma)} · {item.created_at?.slice(0, 10)} ·{' '}
                  {t('demo.tiempo', idioma, { segundos: item.segundos })}
                </div>
                {item.status === 'rejected_quality' ? (
                  <div className="nm-alert nm-alert-warning" style={{ margin: 0 }}>{item.diagnostico}</div>
                ) : (
                  <button
                    type="button"
                    className="nm-btn nm-btn-primary nm-btn-block"
                    onClick={() => onOpenDemoExample(item.generation_id)}
                  >
                    {t('demo.abrir', idioma)}
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Documentos de Biblioteca Demo */}
      <div className="nm-card">
        <h3 style={{ marginTop: 0 }}>{t('onboarding.demo', idioma)}</h3>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.65rem' }}>
          {DOCUMENTOS_DEMO.map((demo) => {
            const doc = demoDocs[demo.id];
            const activo = docActivoDemo && docActivoId === demo.id;
            return (
              <div
                key={demo.id}
                className="nm-card"
                style={{ background: 'var(--surface-2)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.5rem' }}
              >
                <div>
                  <div style={{ fontWeight: 600 }}>{doc ? doc.titulo : t(demo.clave, idioma)}</div>
                  <div style={{ fontSize: '0.8rem', color: 'var(--ink-muted)' }}>
                    {doc ? t(`estados.documento.${doc.status}`, idioma) : `${demo.formato} · ${t('upload.demo_no_disponible', idioma)}`}
                  </div>
                </div>
                <button
                  type="button"
                  className={`nm-btn ${activo ? 'nm-btn-primary' : ''}`}
                  disabled={activo || !doc || doc.status !== 'ready'}
                  onClick={() => onSelectDemoDoc(demo.id)}
                >
                  {t(activo ? 'upload.activo' : 'upload.seleccionar_documento', idioma)}
                </button>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};

const DemoExampleDetailView: React.FC<{
  api: APIClient;
  token: string;
  ejemploId: string;
  idioma: OutputLanguage;
  onBack: () => void;
  onSessionInvalid: () => void;
}> = ({ api, token, ejemploId, idioma, onBack, onSessionInvalid }) => {
  const [paquete, setPaquete] = useState<PedagogicalStudentOutput | null>(null);
  const [itemMeta, setItemMeta] = useState<any | null>(null);
  const [diagrama, setDiagrama] = useState<any | null>(null);
  const [err, setErr] = useState<APIError | null>(null);

  useEffect(() => {
    let active = true;
    setPaquete(null);
    setDiagrama(null);
    Promise.all([api.getDemoGenerations(token), api.getDemoGeneration(token, ejemploId)])
      .then(([cat, pkg]) => {
        if (!active) return;
        const found = (cat?.items || []).find((i: any) => i.generation_id === ejemploId);
        validarPaquete(pkg);
        if (!found || pkg.generation_id !== ejemploId || pkg.documento_fuente.document_id !== found.parametros?.document_id) {
          throw new APIError('INVALID_RESPONSE', 'Ejemplo incompatible con el catálogo.', 502);
        }
        setItemMeta(found);
        setPaquete(pkg);
        if (cat.diagrama?.respuesta?.document_id === pkg.documento_fuente.document_id) {
          const reply = cat.diagrama.respuesta;
          validarRespuestaChat(reply, { document_id: pkg.documento_fuente.document_id,
            pregunta: reply.pregunta, perfil_destinatario: reply.perfil_destinatario,
            idioma_salida: reply.idioma_salida });
          if (reply.document_hash !== pkg.documento_fuente.hash.replace(/^sha256:/, '')) {
            throw new APIError('INVALID_RESPONSE', 'El diagrama corresponde a otra versión de fuente.', 502);
          }
          setDiagrama(cat.diagrama);
        }
      })
      .catch((e) => {
        if (active) setErr(e instanceof APIError ? e : new APIError('INVALID_RESPONSE', 'Ejemplo inválido.', 502));
      });
    return () => { active = false; };
  }, [api, token, ejemploId]);

  const demoProxyClient = useMemo(() => {
    const proxy = Object.create(api);
    proxy.getDocumentSource = (tok: string, _docId: string, chunkId: string) =>
      api.getDemoGenerationSource(tok, ejemploId, chunkId);
    proxy.getDocumentSourceImage = (tok: string, _docId: string, chunkId: string) =>
      api.getDemoGenerationSource(tok, ejemploId, chunkId, { image: true });
    proxy.answerQuiz = (tok: string, _genId: string, payload: any) =>
      api.answerDemoQuiz(tok, ejemploId, payload);
    return proxy as APIClient;
  }, [api, ejemploId]);

  return (
    <div>
      <button type="button" className="nm-btn" style={{ marginBottom: '1rem' }} onClick={onBack}>
        {t('nav.documentos', idioma)}
      </button>
      <ErrorBanner error={err} idioma={idioma} />
      <div className="nm-alert nm-alert-info">
        <strong>{t('historial.ejemplo_guardado', idioma)}</strong> · {t('demo.practica', idioma)}
        {itemMeta && (
          <span style={{ marginLeft: '0.5rem' }}>
            ({itemMeta.created_at?.slice(0, 10)} · {t('demo.tiempo', idioma, { segundos: itemMeta.segundos })})
          </span>
        )}
      </div>
      {paquete && (
        <PackageViewer
          paquete={paquete}
          idioma={idioma}
          apiClient={demoProxyClient}
          token={token}
          registrarEstudio={false}
          onSessionInvalid={onSessionInvalid}
        />
      )}
      {paquete && diagrama && <details className="nm-card">
        <summary>{t('demo.diagrama', idioma)}</summary>
        <p>{diagrama.pregunta || diagrama.respuesta.pregunta}</p>
        <p>{diagrama.respuesta.respuesta}</p>
        <SourcesDrawer documentId={paquete.documento_fuente.document_id} referencias={diagrama.respuesta.referencias}
          idioma={idioma} apiClient={demoProxyClient} token={token} onSessionInvalid={onSessionInvalid} />
      </details>}
    </div>
  );
};
