import React, { useEffect, useMemo, useRef, useState } from 'react';
import { BookMarked, History, MessageSquare, RefreshCw, Search, TrendingUp } from 'lucide-react';
import { APIClient, APIError } from '../api/client';
import {
  FORMATOS,
  IDIOMAS_SALIDA,
  OutputLanguage,
  PedagogicalFormat,
  PedagogicalStudentOutput,
  PERFILES,
  ProgressResponse,
  RecipientProfile,
  Referencia,
} from '../api/types';
import {
  filtrarTerminosGlosario,
  validarGlosario,
  validarPaquete,
  validarGeneracionCompletada,
  validarProgreso,
  validarRespuestaChat,
  validarTrabajoChat,
} from '../flows/validators';
import { t } from '../i18n';
import { ErrorBanner } from './ErrorBanner';
import { ExportDownloads } from './ExportDownloads';
import { PackageViewer } from './FormatRenderers';
import { SourcesDrawer } from './SourcesDrawer';
import { useOtherPendingChat, useScopedState } from '../flows/studyState';

// -----------------------------------------------------------------------------
// 1. Tutor Chat RAG
// -----------------------------------------------------------------------------
interface TurnoChatUI {
  clave: string;
  pregunta: string;
  jobId: string | null;
  status: string;
  respuesta: any | null;
  error: APIError | null;
  cancelacionSolicitada: boolean;
}

export const ChatTutorView: React.FC<{
  apiClient: APIClient;
  token: string;
  workspaceId: string;
  documentId: string;
  documentIds?: string[];
  perfil: RecipientProfile;
  idiomaSalida: OutputLanguage;
  idiomaUi: OutputLanguage;
  onSessionInvalid: () => void;
}> = ({
  apiClient,
  token,
  workspaceId,
  documentId,
  documentIds,
  perfil,
  idiomaSalida,
  idiomaUi,
  onSessionInvalid,
}) => {
  const effectiveDocIds = useMemo(
    () => Array.from(new Set(documentIds && documentIds.length > 0 ? documentIds : [documentId])).sort(),
    [documentId, JSON.stringify(documentIds)]
  );
  const primaryDocId = effectiveDocIds[0] || documentId;
  const scopeKey = `chat:${workspaceId}:${JSON.stringify([effectiveDocIds, perfil, idiomaSalida])}`;
  const [turnos, setTurnos] = useScopedState<TurnoChatUI[]>(scopeKey, []);
  const otroPendiente = useOtherPendingChat(workspaceId, scopeKey);
  const [input, setInput] = useState('');
  const [docReady, setDocReady] = useState<boolean | null>(null);
  const [docError, setDocError] = useState<APIError | null>(null);

  useEffect(() => {
    let active = true;
    setDocError(null);
    Promise.all(effectiveDocIds.map((id) => apiClient.getDocument(token, id)))
      .then((docs) => {
        if (!active) return;
        setDocReady(docs.every((doc) => doc?.status === 'ready'));
      })
      .catch((err) => {
        if (!active) return;
        if (err instanceof APIError) {
          if (err.statusCode === 401) onSessionInvalid();
          if (err.statusCode === 404) setTurnos([]);
          setDocError(err);
        }
      });
    return () => {
      active = false;
    };
  }, [apiClient, token, JSON.stringify(effectiveDocIds), onSessionInvalid]);

  const pendiente =
    turnos.length > 0 && ['admitting', 'queued', 'running'].includes(turnos[turnos.length - 1].status)
      ? turnos[turnos.length - 1]
      : null;

  const consultarPendiente = async (turno: TurnoChatUI) => {
    if (!turno.jobId) return;
    try {
      const datos = await apiClient.getJob(token, turno.jobId);
      validarTrabajoChat(datos, turno.jobId);
      let resValida = turno.respuesta;
      if (datos.status === 'completed') {
        resValida = validarRespuestaChat(datos.result, {
          document_id: primaryDocId,
          pregunta: turno.pregunta,
          perfil_destinatario: perfil,
          idioma_salida: idiomaSalida,
        });
      }
      setTurnos((prev) =>
        prev.map((x) => {
          if (x.clave !== turno.clave) return x;
          return {
            ...x,
            status: datos.status,
            respuesta: resValida,
            error: null,
          };
        })
      );
    } catch (err) {
      if (err instanceof APIError) {
        if (err.statusCode === 401) onSessionInvalid();
        setTurnos((prev) =>
          prev.map((x) => (x.clave === turno.clave ? { ...x, error: err as APIError, status: [401, 404].includes(err.statusCode) ? 'unavailable' : x.status, respuesta: null } : x))
        );
      }
    }
  };

  const recibirTurno = (turno: TurnoChatUI, datos: any): TurnoChatUI => {
    validarTrabajoChat(datos, turno.jobId);
    const respuesta = datos.status === 'completed' ? validarRespuestaChat(datos.result, {
      document_id: documentId, pregunta: turno.pregunta,
      perfil_destinatario: perfil, idioma_salida: idiomaSalida,
    }) : null;
    return { ...turno, jobId: datos.job_id, status: datos.status, respuesta, error: null };
  };

  useEffect(() => {
    if (!pendiente || !pendiente.jobId) return;
    const timer = setInterval(() => {
      consultarPendiente(pendiente);
    }, 5000);
    return () => clearInterval(timer);
  }, [pendiente?.clave, pendiente?.jobId, pendiente?.status]);

  const enviarPregunta = async (e: React.FormEvent) => {
    e.preventDefault();
    const q = input.trim();
    if (!q || pendiente || otroPendiente || docReady !== true || q.length > 2000) return;

    const clave = `chat-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    const nuevo: TurnoChatUI = {
      clave,
      pregunta: q,
      jobId: null,
      status: 'admitting',
      respuesta: null,
      error: null,
      cancelacionSolicitada: false,
    };
    setTurnos((prev) => [...prev.slice(-49), nuevo]);
    setInput('');

    try {
      const recibo = await apiClient.createChat(
        token,
        {
          document_id: primaryDocId,
          document_ids: effectiveDocIds.length > 1 ? effectiveDocIds : undefined,
          pregunta: q,
          perfil_destinatario: perfil,
          idioma_salida: idiomaSalida,
        },
        clave
      );
      const confirmado = recibirTurno(nuevo, recibo);
      setTurnos((prev) =>
        prev.map((x) =>
          x.clave === clave
            ? confirmado
            : x
        )
      );
    } catch (err) {
      if (err instanceof APIError) {
        if (err.statusCode === 401) onSessionInvalid();
        setTurnos((prev) =>
          prev.map((x) => (x.clave === clave ? { ...x, error: err, status: [401, 404].includes(err.statusCode) ? 'unavailable' : x.status, respuesta: null } : x))
        );
      }
    }
  };

  const reenviarAdmision = async (turno: TurnoChatUI) => {
    try {
      const recibo = await apiClient.createChat(
        token,
        {
          document_id: primaryDocId,
          document_ids: effectiveDocIds.length > 1 ? effectiveDocIds : undefined,
          pregunta: turno.pregunta,
          perfil_destinatario: perfil,
          idioma_salida: idiomaSalida,
        },
        turno.clave
      );
      const confirmado = recibirTurno(turno, recibo);
      setTurnos((prev) =>
        prev.map((x) =>
          x.clave === turno.clave
            ? confirmado
            : x
        )
      );
    } catch (err) {
      if (err instanceof APIError) {
        setTurnos((prev) =>
          prev.map((x) => (x.clave === turno.clave ? { ...x, error: err, status: [401, 404].includes(err.statusCode) ? 'unavailable' : x.status, respuesta: null } : x))
        );
      }
    }
  };

  const cancelarTurno = async (turno: TurnoChatUI) => {
    if (!turno.jobId) return;
    try {
      const datos = await apiClient.cancelJob(token, turno.jobId);
      const confirmado = recibirTurno(turno, datos);
      setTurnos((prev) =>
        prev.map((x) =>
          x.clave === turno.clave
            ? {
                ...confirmado,
                cancelacionSolicitada: ['queued', 'running'].includes(datos.status),
              }
            : x
        )
      );
    } catch (err) {
      if (err instanceof APIError) {
        setTurnos((prev) =>
          prev.map((x) => (x.clave === turno.clave ? { ...x, error: err, status: [401, 404].includes(err.statusCode) ? 'unavailable' : x.status, respuesta: null } : x))
        );
      }
    }
  };

  if (otroPendiente) return <div className="nm-alert nm-alert-info">{t('chat.otro_pendiente', idiomaUi)}</div>;
  if (docError) return <ErrorBanner error={docError} idioma={idiomaUi} />;
  if (docReady === false) {
    return <div className="nm-alert nm-alert-info">{t('chat.documento_no_listo', idiomaUi)}</div>;
  }

  return (
    <div className="nm-card">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.5rem', marginBottom: '1rem' }}>
        <div>
          <h3 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <MessageSquare size={18} color="var(--oracle-coral)" />
            {t('chat.titulo', idiomaUi)}
          </h3>
          <div style={{ fontSize: '0.82rem', color: 'var(--ink-muted)' }}>
            {t('chat.parametros', idiomaUi, {
              perfil: t(`perfil.${perfil}`, idiomaUi),
              idioma: t(`idioma.${idiomaSalida}`, idiomaUi),
            })}
          </div>
        </div>
        <button
          type="button"
          className="nm-btn"
          title={t('chat.limpiar_ayuda', idiomaUi)}
          disabled={Boolean(pendiente) || turnos.length === 0}
          onClick={() => setTurnos([])}
        >
          {t('chat.limpiar_vista', idiomaUi)}
        </button>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.85rem', marginBottom: '1rem' }}>
        {turnos.map((turno) => (
          <div key={turno.clave} style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            <div
              style={{
                alignSelf: 'flex-end',
                background: 'rgba(199, 70, 52, 0.22)',
                border: '1px solid var(--border-subtle)',
                padding: '0.65rem 0.9rem',
                borderRadius: '10px 10px 2px 10px',
                maxWidth: '85%',
              }}
            >
              {turno.pregunta}
            </div>

            {turno.error && <ErrorBanner error={turno.error} idioma={idiomaUi} />}

            {turno.respuesta && (
              <div
                style={{
                  alignSelf: 'flex-start',
                  background: 'var(--surface-2)',
                  border: '1px solid var(--border-hairline)',
                  padding: '0.85rem 1rem',
                  borderRadius: '10px 10px 10px 2px',
                  width: '100%',
                }}
              >
                {turno.respuesta.estado === 'fuera_de_alcance' ? (
                  <div className="nm-alert nm-alert-info" style={{ margin: 0 }}>
                    {turno.respuesta.respuesta || t('chat.abstencion', idiomaUi)}
                  </div>
                ) : (
                  <>
                    <div style={{ whiteSpace: 'pre-wrap' }}>{turno.respuesta.respuesta}</div>
                    <SourcesDrawer
                      documentId={documentId}
                      referencias={turno.respuesta.referencias}
                      idioma={idiomaUi}
                      apiClient={apiClient}
                      token={token}
                      onSessionInvalid={onSessionInvalid}
                    />
                  </>
                )}
              </div>
            )}

            {!turno.respuesta && !['admitting', 'queued', 'running'].includes(turno.status) && (
              <div style={{ fontSize: '0.82rem', color: 'var(--ink-muted)' }}>
                {t(turno.status === 'cancelled' ? 'chat.cancelada' : 'chat.sin_respuesta', idiomaUi)}
              </div>
            )}
          </div>
        ))}
      </div>

      {pendiente && (
        <div className="nm-alert nm-alert-info">
          {pendiente.jobId === null ? (
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
              <span>{t('chat.envio_indeterminado', idiomaUi)}</span>
              <button type="button" className="nm-btn" onClick={() => reenviarAdmision(pendiente)}>
                {t('chat.reenviar', idiomaUi)}
              </button>
            </div>
          ) : (
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
              <span>
                {t(pendiente.cancelacionSolicitada ? 'chat.cancelando' : 'chat.pensando', idiomaUi)}
              </span>
              <div style={{ display: 'flex', gap: '0.45rem' }}>
                <button
                  type="button"
                  className="nm-btn"
                  onClick={() => consultarPendiente(pendiente)}
                >
                  {t('chat.actualizar', idiomaUi)}
                </button>
                <button
                  type="button"
                  className="nm-btn nm-btn-danger"
                  disabled={pendiente.cancelacionSolicitada}
                  onClick={() => cancelarTurno(pendiente)}
                >
                  {t('chat.cancelar', idiomaUi)}
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      <form onSubmit={enviarPregunta} style={{ display: 'flex', gap: '0.6rem' }}>
        <input
          type="text"
          className="nm-input"
          placeholder={t('chat.placeholder', idiomaUi)}
          maxLength={2000}
          disabled={Boolean(pendiente)}
          value={input}
          onChange={(e) => setInput(e.target.value)}
        />
        <button
          type="submit"
          className="nm-btn nm-btn-primary"
          disabled={Boolean(pendiente) || !input.trim()}
        >
          {t('chat.enviar', idiomaUi)}
        </button>
      </form>
    </div>
  );
};

// -----------------------------------------------------------------------------
// 2. Glosario Adaptado
// -----------------------------------------------------------------------------
export const GlossaryView: React.FC<{
  apiClient: APIClient;
  token: string;
  documentId: string;
  documentIds?: string[];
  perfil: RecipientProfile;
  idiomaSalida: OutputLanguage;
  idiomaUi: OutputLanguage;
  onSessionInvalid: () => void;
}> = ({ apiClient, token, documentId, documentIds, perfil, idiomaSalida, idiomaUi, onSessionInvalid }) => {
  const effectiveDocIds = useMemo(
    () => Array.from(new Set(documentIds && documentIds.length > 0 ? documentIds : [documentId])).sort(),
    [documentId, JSON.stringify(documentIds)]
  );
  const primaryDocId = effectiveDocIds[0] || documentId;
  const scope = `glossary:${JSON.stringify([token, effectiveDocIds, perfil, idiomaSalida])}`;
  const [jobId, setJobId] = useScopedState<string | null>(scope + ':job', null);
  const [status, setStatus] = useScopedState<string | null>(scope + ':status', null);
  const [pendingKey, setPendingKey] = useScopedState<string | null>(scope + ':key', null);
  const [cancelReq, setCancelReq] = useScopedState(scope + ':cancel', false);
  const [salida, setSalida] = useScopedState<any | null>(scope + ':result', null);
  const [consulta, setConsulta] = useState('');
  const [error, setError] = useState<APIError | null>(null);

  const terminales = new Set(['completed', 'failed', 'rejected_quality', 'cancelled', 'unavailable']);

  const consultar = async (currentJobId: string, currentStatus: string | null) => {
    try {
      let nextStatus = currentStatus;
      if (!nextStatus || !terminales.has(nextStatus)) {
        const recibo = await apiClient.getJob(token, currentJobId);
        validarTrabajoChat(recibo, currentJobId);
        nextStatus = recibo.status;
        setStatus(nextStatus);
      }
      if (nextStatus === 'completed') {
        const glosario = await apiClient.getGlossary(token, currentJobId);
        validarGlosario(glosario, currentJobId, {
          document_id: primaryDocId,
          perfil_destinatario: perfil,
          idioma_salida: idiomaSalida,
        });
        setSalida(glosario);
      } else {
        setSalida(null);
      }
    } catch (err) {
      if (err instanceof APIError) {
        if (err.statusCode === 401) onSessionInvalid();
        if (err.statusCode === 404) {
          setStatus('unavailable');
          setSalida(null);
        }
        setError(err);
      }
    }
  };

  useEffect(() => { setError(null); setConsulta(''); }, [scope]);

  useEffect(() => {
    if (!jobId || !status || terminales.has(status)) return;
    const timer = setInterval(() => {
      consultar(jobId, status);
    }, 5000);
    return () => clearInterval(timer);
  }, [jobId, status]);

  const admitir = async () => {
    setError(null);
    const key = pendingKey || `glos-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
    setPendingKey(key);
    try {
      const recibo = await apiClient.createGlossary(
        token,
        {
          document_id: primaryDocId,
          document_ids: effectiveDocIds.length > 1 ? effectiveDocIds : undefined,
          perfil_destinatario: perfil,
          idioma_salida: idiomaSalida,
        },
        key
      );
      validarTrabajoChat(recibo);
      if (recibo.glossary_id !== recibo.job_id) throw new APIError('INVALID_RESPONSE', 'Admisión de glosario inválida.', 502);
      setJobId(recibo.job_id);
      setStatus(recibo.status);
      setPendingKey(null);
      setCancelReq(false);
      if (recibo.status === 'completed') {
        await consultar(recibo.job_id, 'completed');
      }
    } catch (err) {
      if (err instanceof APIError) {
        if (err.statusCode === 401) onSessionInvalid();
        setError(err);
      }
    }
  };

  const cancelar = async () => {
    if (!jobId) return;
    try {
      await apiClient.cancelJob(token, jobId);
      setCancelReq(true);
    } catch (err) {
      if (err instanceof APIError) setError(err);
    }
  };

  const terminosFiltrados = salida?.terminos
    ? filtrarTerminosGlosario(salida.terminos, consulta)
    : [];

  return (
    <div className="nm-card">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.5rem', marginBottom: '0.85rem' }}>
        <div>
          <h3 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <BookMarked size={18} color="var(--oracle-coral)" />
            {t('glosario.titulo', idiomaUi)}
          </h3>
          <div style={{ fontSize: '0.82rem', color: 'var(--ink-muted)' }}>
            {t('glosario.parametros', idiomaUi, {
              perfil: t(`perfil.${perfil}`, idiomaUi),
              idioma: t(`idioma.${idiomaSalida}`, idiomaUi),
            })}
          </div>
        </div>

        {(!jobId || (status && terminales.has(status))) && (
          <button type="button" className="nm-btn nm-btn-primary" onClick={admitir}>
            {t(pendingKey ? 'glosario.reintentar' : 'glosario.generar', idiomaUi)}
          </button>
        )}
      </div>

      <ErrorBanner error={error} idioma={idiomaUi} />

      {pendingKey && (
        <div className="nm-alert nm-alert-warning">{t('glosario.admision_indeterminada', idiomaUi)}</div>
      )}

      {status && !terminales.has(status) && (
        <div className="nm-alert nm-alert-info">
          <div>{t(cancelReq ? 'glosario.cancelando' : 'glosario.espera', idiomaUi)}</div>
          <div style={{ fontSize: '0.82rem', marginTop: '0.25rem' }}>
            {t(`estados.trabajo.${status}`, idiomaUi)}
          </div>
          <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.6rem' }}>
            <button type="button" className="nm-btn" onClick={() => jobId && consultar(jobId, status)}>
              {t('glosario.actualizar', idiomaUi)}
            </button>
            <button type="button" className="nm-btn nm-btn-danger" disabled={cancelReq} onClick={cancelar}>
              {t('glosario.cancelar', idiomaUi)}
            </button>
          </div>
        </div>
      )}

      {status && ['failed', 'rejected_quality', 'cancelled', 'unavailable'].includes(status) && (
        <div className="nm-alert nm-alert-warning">{t(`glosario.${status}`, idiomaUi)}</div>
      )}

      {!salida && (!status || status === 'completed') && (
        <div className="nm-alert nm-alert-info">{t('glosario.vacio', idiomaUi)}</div>
      )}

      {salida && (
        <div>
          {salida.candidatos_omitidos > 0 && (
            <div className="nm-alert nm-alert-warning">
              {t('glosario.omitidos', idiomaUi, { cantidad: salida.candidatos_omitidos })}
            </div>
          )}

          {salida.estado === 'sin_terminos' ? (
            <div className="nm-alert nm-alert-info">{t('glosario.sin_terminos', idiomaUi)}</div>
          ) : (
            <>
              <div style={{ position: 'relative', marginBottom: '0.6rem' }}>
                <Search size={16} style={{ position: 'absolute', left: '0.75rem', top: '0.68rem', color: 'var(--ink-muted)' }} />
                <input
                  type="text"
                  className="nm-input"
                  style={{ paddingLeft: '2.2rem' }}
                  placeholder={t('glosario.buscar', idiomaUi)}
                  value={consulta}
                  onChange={(e) => setConsulta(e.target.value)}
                />
              </div>
              <div style={{ fontSize: '0.8rem', color: 'var(--ink-muted)', marginBottom: '0.85rem' }}>
                {t('glosario.resultados', idiomaUi, {
                  cantidad: terminosFiltrados.length,
                  total: salida.terminos.length,
                })}
              </div>

              {terminosFiltrados.length === 0 && (
                <div className="nm-alert nm-alert-info">{t('glosario.sin_resultados', idiomaUi)}</div>
              )}

              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                {terminosFiltrados.map((term: any) => (
                  <div key={term.id} className="nm-card" style={{ background: 'var(--surface-2)' }}>
                    <h4 style={{ margin: '0 0 0.25rem 0', color: 'var(--oracle-coral)' }}>
                      {term.termino_original}
                    </h4>
                    {term.estado === 'definido' ? (
                      <>
                        {term.traduccion && (
                          <div style={{ fontSize: '0.8rem', color: 'var(--ink-muted)', marginBottom: '0.35rem' }}>
                            {t('glosario.traduccion', idiomaUi)}: {term.traduccion}
                          </div>
                        )}
                        <p style={{ margin: '0.35rem 0' }}>{term.definicion}</p>
                      </>
                    ) : (
                      <>
                        <div className="nm-alert nm-alert-warning" style={{ margin: '0.35rem 0' }}>
                          {t('glosario.limitacion', idiomaUi)}
                        </div>
                        <p style={{ margin: '0.35rem 0', fontSize: '0.88rem', color: 'var(--ink-secondary)' }}>
                          {term.limitacion}
                        </p>
                      </>
                    )}
                    <SourcesDrawer
                      documentId={documentId}
                      referencias={term.referencias}
                      idioma={idiomaUi}
                      apiClient={apiClient}
                      token={token}
                      onSessionInvalid={onSessionInvalid}
                    />
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
};

// -----------------------------------------------------------------------------
// 3. Panel de Progreso
// -----------------------------------------------------------------------------
export const ProgressView: React.FC<{
  apiClient: APIClient;
  token: string;
  idiomaUi: OutputLanguage;
  pendingEvents: Array<{ event_id: string; datos: Record<string, any> }>;
  onRetryPending: (eventId: string) => Promise<void>;
  onSessionInvalid: () => void;
}> = ({ apiClient, token, idiomaUi, pendingEvents, onRetryPending, onSessionInvalid }) => {
  const [datos, setDatos] = useState<ProgressResponse | null>(null);
  const [error, setError] = useState<APIError | null>(null);
  const [selectedPending, setSelectedPending] = useState<string>('');

  const cargar = async () => {
    setError(null);
    try {
      const salida = await apiClient.getProgress(token);
      validarProgreso(salida);
      setDatos(salida);
    } catch (err) {
      setDatos(null);
      if (err instanceof APIError) {
        if (err.statusCode === 401) {
          onSessionInvalid();
          return;
        }
        setError(err);
      } else {
        setError(new APIError('INVALID_RESPONSE', 'Progreso inválido', 502));
      }
    }
  };

  useEffect(() => {
    cargar();
  }, [token, pendingEvents.length]);

  const targetEventId = selectedPending || pendingEvents[0]?.event_id;

  return (
    <div className="nm-card">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
        <h3 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <TrendingUp size={18} color="var(--oracle-coral)" />
          {t('progreso.titulo', idiomaUi)}
        </h3>
        <button type="button" className="nm-btn" onClick={cargar}>
          <RefreshCw size={14} />
          {t('progreso.actualizar', idiomaUi)}
        </button>
      </div>

      {pendingEvents.length > 0 && (
        <div className="nm-alert nm-alert-warning">
          <div>{t('progreso.pendientes', idiomaUi, { cantidad: pendingEvents.length })}</div>
          {pendingEvents.length > 1 && (
            <select
              className="nm-select"
              style={{ marginTop: '0.5rem' }}
              value={targetEventId}
              onChange={(e) => setSelectedPending(e.target.value)}
            >
              {pendingEvents.map((ev) => (
                <option key={ev.event_id} value={ev.event_id}>
                  {(ev.datos.concepto || ev.datos.flashcard_id) + ' · ' + ev.event_id.slice(-8)}
                </option>
              ))}
            </select>
          )}
          <button
            type="button"
            className="nm-btn nm-btn-primary"
            style={{ marginTop: '0.55rem' }}
            onClick={() => targetEventId && onRetryPending(targetEventId)}
          >
            {t('progreso.reintentar_evento', idiomaUi)}
          </button>
        </div>
      )}

      <ErrorBanner error={error} idioma={idiomaUi} />

      {!datos && error && (
        <div className="nm-alert nm-alert-info">{t('progreso.no_disponible', idiomaUi)}</div>
      )}

      {datos && (
        <>
          <div className="nm-grid-2" style={{ marginBottom: '1rem' }}>
            <div className="nm-card" style={{ background: 'var(--surface-2)' }}>
              <div style={{ fontSize: '0.8rem', color: 'var(--ink-muted)' }}>{t('progreso.conceptos', idiomaUi)}</div>
              <div className="mono" style={{ fontSize: '1.6rem', fontWeight: 700, color: 'var(--oracle-coral)' }}>
                {datos.conceptos_revisados}
              </div>
            </div>
            <div className="nm-card" style={{ background: 'var(--surface-2)' }}>
              <div style={{ fontSize: '0.8rem', color: 'var(--ink-muted)' }}>{t('progreso.flashcards', idiomaUi)}</div>
              <div className="mono" style={{ fontSize: '1.6rem', fontWeight: 700, color: 'var(--oracle-coral)' }}>
                {datos.flashcards_vistas}
              </div>
            </div>
            <div className="nm-card" style={{ background: 'var(--surface-2)' }}>
              <div style={{ fontSize: '0.8rem', color: 'var(--ink-muted)' }}>{t('progreso.primer_intento', idiomaUi)}</div>
              <div className="mono" style={{ fontSize: '1.4rem', fontWeight: 700 }}>
                {datos.quiz_aciertos_primer_intento} / {datos.quiz_total_primer_intento}
              </div>
            </div>
            <div className="nm-card" style={{ background: 'var(--surface-2)' }}>
              <div style={{ fontSize: '0.8rem', color: 'var(--ink-muted)' }}>{t('progreso.aciertos_posteriores', idiomaUi)}</div>
              <div className="mono" style={{ fontSize: '1.4rem', fontWeight: 700 }}>
                {datos.quiz_aciertos_posteriores}
              </div>
            </div>
            <div className="nm-card" style={{ background: 'var(--surface-2)' }}>
              <div style={{ fontSize: '0.8rem', color: 'var(--ink-muted)' }}>{t('progreso.intentos', idiomaUi)}</div>
              <div className="mono" style={{ fontSize: '1.4rem', fontWeight: 700 }}>
                {datos.quiz_intentos}
              </div>
            </div>
            <div className="nm-card" style={{ background: 'var(--surface-2)' }}>
              <div style={{ fontSize: '0.8rem', color: 'var(--ink-muted)' }}>{t('progreso.tiempo_restante', idiomaUi)}</div>
              <div className="mono" style={{ fontSize: '1.4rem', fontWeight: 700, color: '#6ee787' }}>
                {t('progreso.minutos', idiomaUi, { minutos: datos.tiempo_estimado_restante_minutos })}
              </div>
            </div>
          </div>

          <div style={{ fontSize: '0.8rem', color: 'var(--ink-muted)', display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
            <div>{t('progreso.orientativo', idiomaUi)}</div>
            <div>{t('progreso.recuperacion', idiomaUi)}</div>
            <div className="mono">{t('progreso.version', idiomaUi, { version: datos.version })}</div>
          </div>
        </>
      )}
    </div>
  );
};

// -----------------------------------------------------------------------------
// 4. Historial Completo y Comparador Lado a Lado
// -----------------------------------------------------------------------------
export const HistoryFullView: React.FC<{
  apiClient: APIClient;
  token: string;
  workspaceId: string;
  idiomaUi: OutputLanguage;
  onClose: () => void;
  onSessionInvalid: () => void;
}> = ({ apiClient, token, workspaceId, idiomaUi, onClose, onSessionInvalid }) => {
  const [expiraEn, setExpiraEn] = useState<string>('');
  const [docsMap, setDocsMap] = useState<Record<string, string>>({});
  const [filtroDoc, setFiltroDoc] = useState<string>('');
  const [filtroPerfil, setFiltroPerfil] = useState<string>('');
  const [filtroFormato, setFiltroFormato] = useState<string>('');
  const [filtroIdioma, setFiltroIdioma] = useState<string>('');
  const [desde, setDesde] = useState<string>('');
  const [hasta, setHasta] = useState<string>('');
  const [items, setItems] = useState<any[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [anteriores, setAnteriores] = useState<Array<string | null>>([]);
  const [detalleId, setDetalleId] = useState<string | null>(null);
  const [detalleJob, setDetalleJob] = useState<any | null>(null);
  const [compareA, setCompareA] = useState<string>('');
  const [compareB, setCompareB] = useState<string>('');
  const [comparedPackages, setComparedPackages] = useState<PedagogicalStudentOutput[] | null>(null);
  const [compareWarning, setCompareWarning] = useState<string | null>(null);
  const [error, setError] = useState<APIError | null>(null);
  const pageRequest = useRef(0);

  const rangoInvalido = Boolean(desde && hasta && desde > hasta);

  useEffect(() => {
    apiClient
      .workspaceStatus(token)
      .then((ws) => {
        if (ws?.expira_en) setExpiraEn(ws.expira_en.slice(0, 16).replace('T', ' ') + ' UTC');
      })
      .catch(() => {});
    apiClient
      .getDocuments(token, { limit: 100 })
      .then((res) => {
        const m: Record<string, string> = {};
        for (const d of res?.items || []) m[d.document_id] = d.titulo;
        setDocsMap(m);
      })
      .catch(() => {});
  }, [apiClient, token]);

  const cargarPagina = async (targetCursor: string | null) => {
    const request = ++pageRequest.current;
    if (rangoInvalido) return;
    setError(null);
    const params: Record<string, any> = { limit: 20 };
    if (filtroDoc) params.documento = filtroDoc;
    if (filtroPerfil) params.perfil = filtroPerfil;
    if (filtroFormato) params.formato = filtroFormato;
    if (filtroIdioma) params.idioma = filtroIdioma;
    if (desde) params.desde = `${desde}T00:00:00+00:00`;
    if (hasta) params.hasta = `${hasta}T23:59:59.999999+00:00`;
    if (targetCursor) params.cursor = targetCursor;

    try {
      const pagina = await apiClient.getGenerations(token, params);
      if (request !== pageRequest.current) return;
      setItems(pagina.items || []);
      setNextCursor(pagina.next_cursor || null);
    } catch (err) {
      if (request !== pageRequest.current) return;
      if (err instanceof APIError) {
        if (err.statusCode === 401) onSessionInvalid();
        setError(err);
      }
    }
  };

  useEffect(() => {
    setCursor(null);
    setAnteriores([]);
    cargarPagina(null);
    return () => { pageRequest.current++; };
  }, [filtroDoc, filtroPerfil, filtroFormato, filtroIdioma, desde, hasta, apiClient, token]);

  useEffect(() => {
    let active = true;
    setDetalleJob(null);
    if (!detalleId) {
      setDetalleJob(null);
      return;
    }
    apiClient
      .getGeneration(token, detalleId)
      .then((job) => {
        if (!active) return;
        if (job.status === 'completed') validarGeneracionCompletada(job, detalleId);
        setDetalleJob(job);
      })
      .catch((err) => {
        if (!active) return;
        setDetalleJob(null);
        if (err instanceof APIError) setError(err);
      });
    return () => { active = false; };
  }, [detalleId, apiClient, token]);

  useEffect(() => {
    let active = true;
    setCompareWarning(null);
    setComparedPackages(null);
    if (!compareA || !compareB) return;
    if (compareA === compareB) {
      setCompareWarning(t('historial.comparacion_incompatible', idiomaUi));
      return;
    }
    Promise.all([
      apiClient.getGeneration(token, compareA),
      apiClient.getGeneration(token, compareB),
    ])
      .then(([j1, j2]) => {
        if (!active) return;
        if (j1.status !== 'completed' || j2.status !== 'completed' || !j1.contenido || !j2.contenido) {
          setCompareWarning(t('historial.comparacion_incompatible', idiomaUi));
          return;
        }
        validarGeneracionCompletada(j1, compareA);
        validarGeneracionCompletada(j2, compareB);
        const f1 = j1.contenido.documento_fuente;
        const f2 = j2.contenido.documento_fuente;
        if (
          f1.document_id !== f2.document_id ||
          f1.hash !== f2.hash ||
          f1.version !== f2.version
        ) {
          setCompareWarning(t('historial.comparacion_incompatible', idiomaUi));
          return;
        }
        setComparedPackages([j1.contenido, j2.contenido]);
      })
      .catch((err) => {
        if (active && err instanceof APIError) setError(err);
      });
    return () => { active = false; };
  }, [compareA, compareB, idiomaUi, apiClient, token]);

  const sigueCompletado = async (generationId: string): Promise<boolean> => {
    const actual = await apiClient.getGeneration(token, generationId);
    if (actual.status === 'completed') return true;
    setDetalleJob(actual);
    return false;
  };

  const etiquetaFila = (fila: any) => {
    const p = fila.parametros || {};
    const partes = [(fila.created_at || '').slice(0, 16).replace('T', ' ') + ' UTC'];
    if (p.perfil_destinatario) partes.push(t(`perfil.${p.perfil_destinatario}`, idiomaUi));
    if (p.formato_salida) partes.push(t(`formato.${p.formato_salida}`, idiomaUi));
    if (p.idioma_salida) partes.push(t(`idioma.${p.idioma_salida}`, idiomaUi));
    partes.push(t(`modo.${p.modo_generacion ?? 'multiagente'}`, idiomaUi));
    return `${partes.join(' · ')} · ${String(fila.generation_id).slice(-8)}`;
  };

  const completados = items.filter((i) => i.status === 'completed');

  return (
    <div className="nm-card">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
        <h2 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <History size={20} color="var(--oracle-coral)" />
          {t('historial.titulo', idiomaUi)}
        </h2>
        <button type="button" className="nm-btn" onClick={onClose}>
          {t('historial.cerrar', idiomaUi)}
        </button>
      </div>

      {expiraEn && (
        <div style={{ fontSize: '0.82rem', color: 'var(--ink-muted)', marginBottom: '1rem' }}>
          {t('historial.retencion', idiomaUi, { fecha: expiraEn })}
        </div>
      )}

      <ErrorBanner error={error} idioma={idiomaUi} />

      {detalleId ? (
        <div>
          <button
            type="button"
            className="nm-btn"
            style={{ marginBottom: '1rem' }}
            onClick={() => setDetalleId(null)}
          >
            {t('historial.volver', idiomaUi)}
          </button>

          {detalleJob && detalleJob.status !== 'completed' && (
            <div className="nm-alert nm-alert-warning">
              <strong>{t(`estados.trabajo.${detalleJob.status}`, idiomaUi)}</strong>
              {detalleJob.error?.details?.causa && (
                <div style={{ marginTop: '0.35rem' }}>
                  {t(`historial.causa.${detalleJob.error.details.causa}`, idiomaUi)}
                </div>
              )}
              <div style={{ marginTop: '0.35rem', fontSize: '0.82rem' }}>
                {t('historial.sin_borrador', idiomaUi)}
              </div>
            </div>
          )}

          {detalleJob?.status === 'completed' && detalleJob.contenido && (
            <>
              <div style={{ fontSize: '0.8rem', color: 'var(--ink-muted)', marginBottom: '0.5rem' }}>
                {t('historial.fecha_fuente', idiomaUi, {
                  fecha: detalleJob.contenido.created_at.slice(0, 16).replace('T', ' ') + ' UTC',
                  version: detalleJob.contenido.documento_fuente.version,
                })}
              </div>
              <ExportDownloads
                api={apiClient}
                token={token}
                paquete={detalleJob.contenido}
                persistencia={detalleJob.persistencia}
                idioma={idiomaUi}
                onError={setError}
                onSessionInvalid={onSessionInvalid}
                verificarAntes={() => sigueCompletado(detalleJob.generation_id)}
                enTarjeta={false}
              />

              <PackageViewer
                paquete={detalleJob.contenido}
                idioma={idiomaUi}
                apiClient={apiClient}
                token={token}
                onSessionInvalid={onSessionInvalid}
              />
            </>
          )}
        </div>
      ) : (
        <>
          {/* Filtros */}
          <div className="nm-card" style={{ background: 'var(--surface-2)', marginBottom: '1rem' }}>
            <div style={{ fontWeight: 600, marginBottom: '0.65rem' }}>{t('historial.filtros', idiomaUi)}</div>
            <div className="nm-grid-4">
              <div>
                <label className="nm-label" htmlFor="study-field-1">{t('historial.documento', idiomaUi)}</label>
                <select id="study-field-1" className="nm-select" value={filtroDoc} onChange={(e) => setFiltroDoc(e.target.value)}>
                  <option value="">{t('historial.todos', idiomaUi)}</option>
                  {Object.entries(docsMap).map(([id, title]) => (
                    <option key={id} value={id}>{title}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="nm-label" htmlFor="study-field-2">{t('historial.perfil', idiomaUi)}</label>
                <select id="study-field-2" className="nm-select" value={filtroPerfil} onChange={(e) => setFiltroPerfil(e.target.value)}>
                  <option value="">{t('historial.todos', idiomaUi)}</option>
                  {PERFILES.map((p) => (
                    <option key={p} value={p}>{t(`perfil.${p}`, idiomaUi)}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="nm-label" htmlFor="study-field-3">{t('historial.formato', idiomaUi)}</label>
                <select id="study-field-3" className="nm-select" value={filtroFormato} onChange={(e) => setFiltroFormato(e.target.value)}>
                  <option value="">{t('historial.todos', idiomaUi)}</option>
                  {FORMATOS.map((f) => (
                    <option key={f} value={f}>{t(`formato.${f}`, idiomaUi)}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="nm-label" htmlFor="study-field-4">{t('historial.idioma', idiomaUi)}</label>
                <select id="study-field-4" className="nm-select" value={filtroIdioma} onChange={(e) => setFiltroIdioma(e.target.value)}>
                  <option value="">{t('historial.todos', idiomaUi)}</option>
                  {IDIOMAS_SALIDA.map((l) => (
                    <option key={l} value={l}>{t(`idioma.${l}`, idiomaUi)}</option>
                  ))}
                </select>
              </div>
            </div>

            <div className="nm-grid-2" style={{ marginTop: '0.65rem' }}>
              <div>
                <label className="nm-label" htmlFor="study-field-5">{t('historial.desde', idiomaUi)}</label>
                <input id="study-field-5" type="date" className="nm-input" value={desde} onChange={(e) => setDesde(e.target.value)} />
              </div>
              <div>
                <label className="nm-label" htmlFor="study-field-6">{t('historial.hasta', idiomaUi)}</label>
                <input id="study-field-6" type="date" className="nm-input" value={hasta} onChange={(e) => setHasta(e.target.value)} />
              </div>
            </div>
            {rangoInvalido && (
              <div className="nm-alert nm-alert-warning" style={{ marginTop: '0.65rem', marginBottom: 0 }}>
                {t('historial.rango_invalido', idiomaUi)}
              </div>
            )}
          </div>

          {items.length === 0 ? (
            <div className="nm-alert nm-alert-info">{t('historial.vacio', idiomaUi)}</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.65rem', marginBottom: '1rem' }}>
              {items.map((fila) => (
                <div
                  key={fila.generation_id}
                  className="nm-card"
                  style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.75rem' }}
                >
                  <div>
                    <div style={{ fontWeight: 600 }}>{etiquetaFila(fila)}</div>
                    <div style={{ fontSize: '0.8rem', color: 'var(--ink-muted)' }}>
                      {t('historial.documento', idiomaUi)}: {docsMap[fila.document_id] || fila.document_id} ·{' '}
                      <span style={{ color: 'var(--oracle-coral)' }}>
                        {t(`estados.trabajo.${fila.status}`, idiomaUi)}
                      </span>
                    </div>
                  </div>
                  <button
                    type="button"
                    className="nm-btn"
                    onClick={() => setDetalleId(fila.generation_id)}
                  >
                    {t('historial.abrir_resultado', idiomaUi)}
                  </button>
                </div>
              ))}
            </div>
          )}

          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '1.5rem' }}>
            <button
              type="button"
              className="nm-btn"
              disabled={anteriores.length === 0}
              onClick={() => {
                const copia = [...anteriores];
                const prev = copia.pop() ?? null;
                setAnteriores(copia);
                setCursor(prev);
                cargarPagina(prev);
              }}
            >
              {t('historial.anterior', idiomaUi)}
            </button>
            <button
              type="button"
              className="nm-btn"
              disabled={!nextCursor}
              onClick={() => {
                if (!nextCursor) return;
                setAnteriores((prev) => [...prev, cursor]);
                setCursor(nextCursor);
                cargarPagina(nextCursor);
              }}
            >
              {t('historial.siguiente', idiomaUi)}
            </button>
          </div>

          {/* Comparador lado a lado */}
          {completados.length >= 2 && (
            <div style={{ borderTop: '1px solid var(--border-hairline)', paddingTop: '1.25rem' }}>
              <h3 style={{ margin: '0 0 0.75rem 0' }}>{t('historial.comparar', idiomaUi)}</h3>
              <div className="nm-grid-2" style={{ marginBottom: '1rem' }}>
                <div>
                  <label className="nm-label" htmlFor="study-field-7">{t('historial.adaptacion_0', idiomaUi)}</label>
                  <select id="study-field-7" className="nm-select" value={compareA} onChange={(e) => setCompareA(e.target.value)}>
                    <option value="">{t('historial.elegir', idiomaUi)}</option>
                    {completados.map((c) => (
                      <option key={c.generation_id} value={c.generation_id}>{etiquetaFila(c)}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="nm-label" htmlFor="study-field-8">{t('historial.adaptacion_1', idiomaUi)}</label>
                  <select id="study-field-8" className="nm-select" value={compareB} onChange={(e) => setCompareB(e.target.value)}>
                    <option value="">{t('historial.elegir', idiomaUi)}</option>
                    {completados.map((c) => (
                      <option key={c.generation_id} value={c.generation_id}>{etiquetaFila(c)}</option>
                    ))}
                  </select>
                </div>
              </div>

              {compareWarning && <div className="nm-alert nm-alert-warning">{compareWarning}</div>}

              {comparedPackages && (
                <div className="nm-grid-2">
                  {comparedPackages.map((pkg) => (
                    <div key={pkg.generation_id} className="nm-card" style={{ background: 'var(--surface-2)' }}>
                      <PackageViewer
                        paquete={pkg}
                        idioma={idiomaUi}
                        apiClient={apiClient}
                        token={token}
                        registrarEstudio={false}
                        onSessionInvalid={onSessionInvalid}
                      />
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
};
