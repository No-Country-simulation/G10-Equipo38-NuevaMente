import React, { useState, useRef, useEffect } from 'react';
import { BookOpen, Eye, EyeOff, Image as ImageIcon } from 'lucide-react';
import { APIClient, APIError } from '../api/client';
import { OutputLanguage, Referencia } from '../api/types';
import { validarFuente } from '../flows/validators';
import { t } from '../i18n';
import { ErrorBanner } from './ErrorBanner';

const SourcesDrawerView: React.FC<{
  documentId: string;
  referencias: Referencia[];
  idioma: OutputLanguage;
  apiClient: APIClient;
  token: string | null;
  demo?: boolean;
  onSessionInvalid: () => void;
}> = ({ documentId, referencias, idioma, apiClient, token, demo = false, onSessionInvalid }) => {
  const [openChunks, setOpenChunks] = useState<Record<string, boolean>>({});
  const [loadedSources, setLoadedSources] = useState<Record<string, any>>({});
  const [loadedImages, setLoadedImages] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<Record<string, APIError | null>>({});
  const [loading, setLoading] = useState<Record<string, boolean>>({});
  const urls = useRef(new Set<string>());
  const active = useRef(true);
  useEffect(() => {
    active.current = true;
    return () => { active.current = false; urls.current.forEach((url) => URL.revokeObjectURL(url)); };
  }, []);

  // Deduplicar por chunk_id preservando orden
  const uniqueRefs: Referencia[] = [];
  const seen = new Set<string>();
  for (const r of referencias || []) {
    if (r?.chunk_id && !seen.has(r.chunk_id)) {
      seen.add(r.chunk_id);
      uniqueRefs.push(r);
    }
  }

  const toggleChunk = async (ref: Referencia) => {
    const cid = ref.chunk_id;
    const isCurrentlyOpen = Boolean(openChunks[cid]);
    if (isCurrentlyOpen) {
      setOpenChunks((prev) => ({ ...prev, [cid]: false }));
      return;
    }

    setOpenChunks((prev) => ({ ...prev, [cid]: true }));
    if (!token) return;

    setLoading((prev) => ({ ...prev, [cid]: true }));
    setErrors((prev) => ({ ...prev, [cid]: null }));
    const targetDocId = ref.document_id || documentId;
    try {
      const fuente = await apiClient.getDocumentSource(token, targetDocId, cid, { demo });
      if (!active.current) return;
      validarFuente(fuente, ref, targetDocId);
      setLoadedSources((prev) => ({ ...prev, [cid]: fuente }));

      const esInterpretacion =
        fuente.source_type === 'image_description' || Boolean(fuente.interpretacion_modelo);
      if (esInterpretacion && !demo) {
        const pngBytes = await apiClient.getDocumentSourceImage(token, targetDocId, cid);
        if (!active.current) return;
        const blob = new Blob([pngBytes], { type: 'image/png' });
        const objUrl = URL.createObjectURL(blob);
        urls.current.add(objUrl);
        setLoadedImages((prev) => {
          if (prev[cid]) { URL.revokeObjectURL(prev[cid]); urls.current.delete(prev[cid]); }
          return { ...prev, [cid]: objUrl };
        });
      }
    } catch (err) {
      if (err instanceof APIError) {
        if (err.statusCode === 401) {
          onSessionInvalid();
          return;
        }
        setErrors((prev) => ({ ...prev, [cid]: err }));
      } else {
        setErrors((prev) => ({
          ...prev,
          [cid]: new APIError('INVALID_RESPONSE', 'Fuente incompatible.', 502),
        }));
      }
    } finally {
      setLoading((prev) => ({ ...prev, [cid]: false }));
    }
  };

  if (uniqueRefs.length === 0) return null;

  return (
    <div style={{ marginTop: '0.75rem', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
      {uniqueRefs.map((ref, idx) => {
        const cid = ref.chunk_id;
        const isOpen = Boolean(openChunks[cid]);
        const fuente = loadedSources[cid];
        const imgUrl = loadedImages[cid];
        const err = errors[cid];
        const isBusy = Boolean(loading[cid]);

        const partesRotulo = [t('fuentes.fragmento', idioma, { numero: idx + 1 })];
        if (ref.pagina) partesRotulo.push(t('fuentes.pagina', idioma, { numero: ref.pagina }));
        if (ref.seccion) partesRotulo.push(ref.seccion);

        return (
          <div
            key={cid}
            style={{
              background: 'var(--surface-2)',
              border: '1px solid var(--border-hairline)',
              borderRadius: 'var(--radius-sm)',
              padding: '0.65rem 0.85rem',
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
              <span style={{ fontSize: '0.83rem', color: 'var(--ink-secondary)', display: 'inline-flex', alignItems: 'center', gap: '0.4rem' }}>
                <BookOpen size={14} color="var(--oracle-coral)" />
                <strong>{t('quiz.ver_fuente', idioma)}</strong> · {partesRotulo.join(' · ')}
              </span>
              <button
                type="button"
                className="nm-btn"
                style={{ padding: '0.3rem 0.65rem', fontSize: '0.8rem' }}
                disabled={!token || isBusy}
                onClick={() => toggleChunk(ref)}
              >
                {isOpen ? <EyeOff size={14} /> : <Eye size={14} />}
                {t(isOpen ? 'fuentes.cerrar' : 'fuentes.cargar', idioma)}
              </button>
            </div>

            {isOpen && (
              <div style={{ marginTop: '0.65rem', borderTop: '1px solid var(--border-hairline)', paddingTop: '0.65rem' }}>
                {isBusy && <div style={{ fontSize: '0.84rem', color: 'var(--ink-muted)' }}>{t('comun.cargando', idioma)}</div>}
                {err && <ErrorBanner error={err} idioma={idioma} />}
                {!isBusy && !err && fuente && (
                  <div>
                    {fuente.source_type === 'image_description' || fuente.interpretacion_modelo ? (
                      <div style={{ marginBottom: '0.6rem' }}>
                        <div style={{ fontSize: '0.8rem', color: 'var(--oracle-amber)', display: 'flex', alignItems: 'center', gap: '0.35rem', marginBottom: '0.4rem' }}>
                          <ImageIcon size={14} />
                          {t('fuentes.interpretacion', idioma)}
                        </div>
                        {demo ? (
                          <div className="nm-alert nm-alert-warning">{t('fuentes.imagen_no_disponible', idioma)}</div>
                        ) : (
                          imgUrl && (
                            <figure style={{ margin: '0 0 0.65rem 0' }}>
                              <img
                                src={imgUrl}
                                alt={t('fuentes.imagen_original', idioma)}
                                style={{ maxWidth: '100%', borderRadius: '6px', border: '1px solid var(--border-hairline)' }}
                              />
                              <figcaption style={{ fontSize: '0.78rem', color: 'var(--ink-muted)', marginTop: '0.25rem' }}>
                                {t('fuentes.imagen_original', idioma)}
                              </figcaption>
                            </figure>
                          )
                        )}
                      </div>
                    ) : (
                      <div style={{ fontSize: '0.78rem', color: 'var(--ink-muted)', marginBottom: '0.35rem' }}>
                        {t('fuentes.texto_original', idioma)}
                      </div>
                    )}

                    <div
                      style={{
                        whiteSpace: 'pre-wrap',
                        background: 'var(--surface-1)',
                        padding: '0.75rem',
                        borderRadius: '6px',
                        borderLeft: '3px solid var(--oracle-red)',
                        fontSize: '0.88rem',
                      }}
                    >
                      {fuente.texto}
                    </div>

                    <div style={{ display: 'flex', gap: '0.75rem', marginTop: '0.4rem', fontSize: '0.78rem', color: 'var(--ink-muted)' }}>
                      {fuente.pagina && <span>{t('fuentes.pagina', idioma, { numero: fuente.pagina })}</span>}
                      {fuente.seccion && <span>{fuente.seccion}</span>}
                      <span className="mono">{fuente.chunk_id}</span>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
};

export const SourcesDrawer: React.FC<React.ComponentProps<typeof SourcesDrawerView>> = (props) =>
  <SourcesDrawerView key={`${props.token}:${props.documentId}:${props.demo}`} {...props} />;
