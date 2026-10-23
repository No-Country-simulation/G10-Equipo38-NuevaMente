import React, { useState } from 'react';
import { AlertTriangle, ChevronDown, ChevronRight } from 'lucide-react';
import { APIError } from '../api/client';
import { OutputLanguage } from '../api/types';
import { CATALOGOS, t } from '../i18n';

export function mensajeError(error: APIError, idioma: OutputLanguage): string {
  let clave = `errors.${error.code}`;
  if (!(clave in CATALOGOS.es)) {
    clave = 'errors.INTERNAL';
  }
  const motivo = error.details?.motivo;
  const parserCode = error.details?.parser_code;
  if (
    typeof parserCode === 'string' &&
    `upload.parser_${parserCode.toLowerCase()}` in CATALOGOS.es
  ) {
    clave = `upload.parser_${parserCode.toLowerCase()}`;
  }
  if (motivo === 'workspace_resource_limit') {
    const limite = error.details?.limite;
    const recurso = error.details?.recurso;
    if (limite !== undefined && limite !== null) {
      if (recurso === 'documents') {
        return t('presupuesto.limite_documentos', idioma, { limite });
      }
      if (recurso === 'generations') {
        return t('presupuesto.limite_generaciones', idioma, { limite });
      }
      return t('presupuesto.limite_recursos_con_limite', idioma, { limite });
    }
    clave = 'presupuesto.limite_recursos';
  } else if (motivo === 'storage_budget') {
    clave = 'presupuesto.bloqueado';
  }
  return t(clave, idioma);
}

export const ErrorBanner: React.FC<{
  error: APIError | null;
  idioma: OutputLanguage;
  onDismiss?: () => void;
}> = ({ error, idioma, onDismiss }) => {
  const [open, setOpen] = useState(false);
  if (!error) return null;

  return (
    <div className="nm-alert nm-alert-error" role="alert">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '0.75rem' }}>
        <div style={{ display: 'flex', gap: '0.55rem', alignItems: 'center' }}>
          <AlertTriangle size={18} />
          <div>
            <strong>{t('errors.titulo', idioma)}:</strong> {mensajeError(error, idioma)}
          </div>
        </div>
        {onDismiss && (
          <button type="button" className="nm-btn" style={{ padding: '0.2rem 0.5rem' }} onClick={onDismiss}>
            {t('comun.cerrar', idioma)}
          </button>
        )}
      </div>

      {error.retryAfter && /^\d+$/.test(error.retryAfter) && (
        <div style={{ fontSize: '0.82rem', marginTop: '0.35rem', color: 'var(--ink-secondary)' }}>
          {t('errors.reintentar_en', idioma, { segundos: error.retryAfter })}
        </div>
      )}

      <div style={{ marginTop: '0.5rem' }}>
        <button
          type="button"
          onClick={() => setOpen(!open)}
          style={{
            background: 'none',
            border: 'none',
            color: 'var(--ink-secondary)',
            cursor: 'pointer',
            display: 'inline-flex',
            alignItems: 'center',
            gap: '0.25rem',
            fontSize: '0.8rem',
            padding: 0,
          }}
        >
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          {t('comun.detalles_tecnicos', idioma)}
        </button>
        {open && (
          <pre
            className="mono"
            style={{
              marginTop: '0.4rem',
              padding: '0.5rem',
              background: 'rgba(0,0,0,0.35)',
              borderRadius: '6px',
              fontSize: '0.78rem',
              overflowX: 'auto',
            }}
          >
            {`Code: ${error.code}\nRequest-ID: ${error.requestId || 'N/A'}`}
          </pre>
        )}
      </div>
    </div>
  );
};
