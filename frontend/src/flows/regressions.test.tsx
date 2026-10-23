import React, { StrictMode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { App } from '../App';
import { APIClient } from '../api/client';
import { ChatTutorView, GlossaryView, HistoryFullView } from '../components/StudyAndHistoryViews';
import { SourcesDrawer } from '../components/SourcesDrawer';
import { claveCarga, generarEventIdProgreso, validarGeneracionCompletada, validarPaquete, validarGlosario } from './validators';
import fixtures from './fixtures.test-data.json';
import { PackageViewer } from '../components/FormatRenderers';
import { clearStudyState } from './studyState';
import { t } from '../i18n';

beforeEach(() => {
  sessionStorage.clear();
  clearStudyState();
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('Network disabled in unit tests')));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe('intenciones durables y fronteras', () => {
  it('descarta la página antigua si el filtro cambia antes de recibirla', async () => {
    let resolveOld!: (value: any) => void;
    const oldPage = new Promise((resolve) => { resolveOld = resolve; });
    const api = { workspaceStatus: vi.fn().mockResolvedValue(null),
      getDocuments: vi.fn().mockResolvedValue({items:[]}),
      getGenerations: vi.fn().mockReturnValueOnce(oldPage).mockResolvedValue({items:[{
        generation_id:'gen-current',document_id:'doc-actual',status:'queued',created_at:'2026-10-15T00:00:00Z',
      }],next_cursor:null}),
    } as unknown as APIClient;
    render(<HistoryFullView apiClient={api} token="synthetic" workspaceId="ws"
      idiomaUi="es" onClose={vi.fn()} onSessionInvalid={vi.fn()} />);
    fireEvent.change(screen.getByLabelText(t('historial.perfil','es')), {target:{value:'principiante'}});
    await screen.findByText(/doc-actual/);
    await act(async () => { resolveOld({items:[{
      generation_id:'gen-old',document_id:'doc-anterior',status:'queued',created_at:'2026-10-14T00:00:00Z',
    }],next_cursor:null}); });
    expect(screen.queryByText(/doc-anterior/)).toBeNull();
    expect(screen.getByText(/doc-actual/)).toBeTruthy();
  });
  it('no conserva una cita abierta al cambiar de documento con el mismo chunk_id', async () => {
    const api = { getDocumentSource: vi.fn().mockResolvedValue({ document_id:'doc-a', chunk_id:'chunk',
      texto:'Evidencia exclusiva del documento A', pagina:null, seccion:null }) } as unknown as APIClient;
    const props = { referencias:[{chunk_id:'chunk'}], idioma:'es' as const, apiClient:api,
      token:'synthetic', onSessionInvalid:vi.fn() };
    const view = render(<SourcesDrawer {...props} documentId="doc-a" />);
    fireEvent.click(screen.getByRole('button',{name:t('fuentes.cargar','es')}));
    await screen.findByText('Evidencia exclusiva del documento A');
    view.rerender(<SourcesDrawer {...props} documentId="doc-b" />);
    expect(screen.queryByText('Evidencia exclusiva del documento A')).toBeNull();
  });

  it('muestra un glosario aprobado en caché y lo aísla por perfil', async () => {
    const api = { createGlossary:vi.fn().mockResolvedValue({job_id:'glos',glossary_id:'glos',status:'completed'}),
      getGlossary:vi.fn().mockResolvedValue({ glossary_id:'glos', documento_fuente:{document_id:'doc-a'},
        perfil_destinatario:'principiante',idioma_salida:'es',estado:'verificado',candidatos_omitidos:0,
        evaluacion_calidad:fixtures.flashcards.evaluacion_calidad,trazabilidad:fixtures.flashcards.trazabilidad,
        terminos:[{id:'term',termino_original:'VCN',es_identificador:true,traduccion:null,estado:'definido',
          definicion:'Definición sintética respaldada',limitacion:null,referencias:[{chunk_id:'chunk'}]}],
      }) } as unknown as APIClient;
    const props = {apiClient:api,token:'synthetic',documentId:'doc-a',idiomaUi:'es' as const,
      idiomaSalida:'es' as const,onSessionInvalid:vi.fn()};
    const view = render(<GlossaryView {...props} perfil="principiante" />);
    fireEvent.click(screen.getByRole('button',{name:'Generar glosario'}));
    await screen.findByText('Definición sintética respaldada');
    view.rerender(<GlossaryView {...props} perfil="ejecutivo" />);
    expect(screen.queryByText('Definición sintética respaldada')).toBeNull();
    view.rerender(<GlossaryView {...props} perfil="principiante" />);
    expect(screen.getByText('Definición sintética respaldada')).toBeTruthy();
    expect(api.createGlossary).toHaveBeenCalledTimes(1);
  });
  it('reinicia respuestas visibles al abrir otra generación del mismo formato', () => {
    const first = fixtures.flashcards;
    validarPaquete(first);
    const props = { idioma: 'es' as const, apiClient: new APIClient(), token: 'synthetic', onSessionInvalid: vi.fn() };
    const view = render(<PackageViewer {...props} paquete={{...first, generation_id:'gen-first'}} />);
    fireEvent.click(screen.getByRole('button', {name:t('material.mostrar_respuesta','es')}));
    expect(screen.getByText(first.contenido_adaptado.items[0].dorso)).toBeTruthy();
    view.rerender(<PackageViewer {...props} paquete={{...first, generation_id:'gen-second'}} />);
    expect(screen.queryByText(first.contenido_adaptado.items[0].dorso)).toBeNull();
  });
  it('mantiene el timeout activo hasta terminar de leer el cuerpo HTTP', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn(async (_url, options) => ({ status: 200, ok: true,
      type: 'basic', headers: new Headers(), json: () => new Promise((_resolve, reject) => {
        options.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
      }),
    })));
    const pending = expect(new APIClient().getDocuments('synthetic')).rejects.toMatchObject({code:'SERVICE_UNAVAILABLE'});
    await vi.advanceTimersByTimeAsync(30001);
    await pending;
  });
  it.each(Object.entries(fixtures))('renderiza el contrato estudiante %s sin llamadas externas', (_name, data) => {
    validarPaquete(data);
    render(<PackageViewer paquete={data} idioma="es" apiClient={new APIClient()} token="synthetic"
      onSessionInvalid={vi.fn()} />);
    expect(screen.getByRole('heading', {name: data.contenido_adaptado.titulo})).toBeTruthy();
    expect(fetch).not.toHaveBeenCalled();
    if (data.contenido_adaptado.tipo === 'quiz') {
      expect('correct_option_id' in data.contenido_adaptado.preguntas[0]).toBe(false);
    }
  });

  it('acepta términos sin definición y sin citas como limitaciones, sin inventar calidad', () => {
    expect(() => validarGlosario({ glossary_id: 'glos', documento_fuente: { document_id: 'doc' },
      perfil_destinatario: 'principiante', idioma_salida: 'es', candidatos_omitidos: 0,
      estado: 'sin_definiciones', evaluacion_calidad: null,
      terminos: [{ id: 'term', termino_original: 'VCN', es_identificador: true,
        estado: 'sin_definicion_suficiente', limitacion: 'Evidencia insuficiente',
        definicion: null, traduccion: null, referencias: [] }],
    }, 'glos', { document_id: 'doc', perfil_destinatario: 'principiante', idioma_salida: 'es' })).not.toThrow();
  });
  it('distingue archivos con igual nombre y tamaño, y conserva la clave al reintentar', async () => {
    const first = await claveCarga('fuente.txt', new Uint8Array([1, 2]));
    expect(await claveCarga('fuente.txt', new Uint8Array([1, 2]))).toBe(first);
    expect(await claveCarga('fuente.txt', new Uint8Array([2, 1]))).not.toBe(first);
    expect(await claveCarga('otro.txt', new Uint8Array([1, 2]))).not.toBe(first);
    expect(first.length).toBeLessThanOrEqual(128);
  });

  it('conserva el identificador de progreso del cliente Python', async () => {
    const canonical = '["ws", {"concepto": "Red", "document_id": "doc", "tipo": "concepto_revisado"}]';
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(canonical));
    const expected = [...new Uint8Array(digest)].map((v) => v.toString(16).padStart(2, '0')).join('');
    expect(await generarEventIdProgreso('ws', { tipo: 'concepto_revisado', document_id: 'doc', concepto: 'Red' }))
      .toBe('ui41-' + expected);
  });

  it('bloquea una generación sin persistencia confirmada', () => {
    expect(() => validarGeneracionCompletada({ generation_id: 'gen', status: 'completed',
      persistencia: { status_upload: 'pendiente', provider: 'oci' } }, 'gen')).toThrow();
  });

  it('crea un único espacio bajo StrictMode y mantiene el token fuera de Web Storage', async () => {
    const create = vi.spyOn(APIClient.prototype, 'createWorkspace').mockResolvedValue({
      workspace_id: 'ws', token: 'synthetic-token', recovery_code: 'synthetic-recovery',
    });
    vi.spyOn(APIClient.prototype, 'getDocuments').mockResolvedValue({ items: [], next_cursor: null });
    vi.spyOn(APIClient.prototype, 'workspaceStatus').mockResolvedValue(null);
    vi.spyOn(APIClient.prototype, 'getProgress').mockResolvedValue({ version: 0,
      conceptos_revisados: 0, flashcards_vistas: 0, quiz_intentos: 0,
      quiz_aciertos_primer_intento: 0, quiz_total_primer_intento: 0,
      quiz_aciertos_posteriores: 0, tiempo_estimado_restante_minutos: 0 });
    render(<StrictMode><App /></StrictMode>);
    await screen.findByText('synthetic-recovery');
    expect(create).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem('nm_session_token')).toBeNull();
    expect(localStorage.getItem('nm_session_token')).toBeNull();
  });
});

describe('aislamiento del tutor', () => {
  const base = { token: 'synthetic-token', workspaceId: 'ws', perfil: 'principiante' as const,
    idiomaSalida: 'es' as const, idiomaUi: 'es' as const, onSessionInvalid: vi.fn() };
  const setup = (response: any) => {
    const api = { getDocument: vi.fn().mockResolvedValue({ status: 'ready' }),
      createChat: vi.fn().mockResolvedValue(response) } as unknown as APIClient;
    return api;
  };
  const send = async () => {
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '¿Qué es una red?' } });
    await waitFor(() => expect(screen.getByRole('button', { name: t('chat.enviar', 'es') }).hasAttribute('disabled')).toBe(false));
    fireEvent.submit(screen.getByRole('textbox').closest('form')!);
  };

  it('oculta la conversación ajena y conserva la intención pendiente al volver', async () => {
    const api = setup({ job_id: 'job-a', status: 'queued' });
    const view = render(<ChatTutorView {...base} apiClient={api} documentId="doc-a" />);
    await send();
    await waitFor(() => expect(api.createChat).toHaveBeenCalledTimes(1));
    view.rerender(<ChatTutorView {...base} apiClient={api} documentId="doc-b" />);
    expect(screen.queryByText('¿Qué es una red?')).toBeNull();
    expect(screen.getByText(t('chat.otro_pendiente', 'es'))).toBeTruthy();
    view.rerender(<ChatTutorView {...base} apiClient={api} documentId="doc-a" />);
    expect(screen.getByText('¿Qué es una red?')).toBeTruthy();
    expect(api.createChat).toHaveBeenCalledTimes(1);
  });

  it('valida también el resultado recibido directamente en la admisión', async () => {
    const api = setup({ job_id: 'job-a', status: 'completed', result: {
      document_id: 'otro-documento', respuesta: 'contenido que debe bloquearse',
    } });
    render(<ChatTutorView {...base} apiClient={api} documentId="doc-a" />);
    await send();
    await screen.findByRole('alert');
    expect(screen.queryByText('contenido que debe bloquearse')).toBeNull();
  });

  it('publica una respuesta inmediata cuando coincide con la pregunta y su evaluación está aprobada', async () => {
    const api = setup({job_id:'job-a',status:'completed',result:{
      document_id:'doc-a',document_hash:'a'.repeat(64),pregunta:'¿Qué es una red?',
      perfil_destinatario:'principiante',idioma_salida:'es',estado:'respondida',
      respuesta:'Respuesta sintética verificada',referencias:[{chunk_id:'chunk'}],
      evaluacion_calidad:fixtures.flashcards.evaluacion_calidad,
    }});
    render(<ChatTutorView {...base} apiClient={api} documentId="doc-a" />);
    await send();
    await screen.findByText('Respuesta sintética verificada');
  });

  it('envía document_ids ordenados al tutor cuando se especifican múltiples fuentes', async () => {
    const api = setup({
      job_id: 'job-m', status: 'completed', result: {
        document_id: 'doc-a', document_ids: ['doc-a', 'doc-b'], document_hash: 'a'.repeat(64),
        pregunta: '¿Qué es una red?', perfil_destinatario: 'principiante', idioma_salida: 'es',
        estado: 'respondida', respuesta: 'Respuesta multidocumental', referencias: [{ chunk_id: 'chunk', document_id: 'doc-a' }],
        evaluacion_calidad: fixtures.flashcards.evaluacion_calidad,
      }
    });
    render(<ChatTutorView {...base} apiClient={api} documentId="doc-a" documentIds={['doc-b', 'doc-a']} />);
    await send();
    await screen.findByText('Respuesta multidocumental');
    expect(api.createChat).toHaveBeenCalledWith(
      'synthetic-token',
      expect.objectContaining({
        document_id: 'doc-a',
        document_ids: ['doc-a', 'doc-b'],
      }),
      expect.any(String)
    );
  });

  it('envía document_ids al glosario cuando se especifican múltiples fuentes', async () => {
    const api = {
      getDocument: vi.fn().mockResolvedValue({ status: 'ready' }),
      createGlossary: vi.fn().mockResolvedValue({ job_id: 'glos-m', glossary_id: 'glos-m', status: 'completed' }),
      getGlossary: vi.fn().mockResolvedValue({
        glossary_id: 'glos-m', documento_fuente: { document_id: 'doc-a' },
        documentos_fuente: [{ document_id: 'doc-a' }, { document_id: 'doc-b' }],
        perfil_destinatario: 'principiante', idioma_salida: 'es', estado: 'verificado', candidatos_omitidos: 0,
        evaluacion_calidad: fixtures.flashcards.evaluacion_calidad, trazabilidad: fixtures.flashcards.trazabilidad,
        terminos: [{ id: 'term-m', termino_original: 'VCN', es_identificador: true, traduccion: null, estado: 'definido',
          definicion: 'Definición multidocumental', limitacion: null, referencias: [{ chunk_id: 'chunk', document_id: 'doc-b' }] }],
      }),
    } as unknown as APIClient;
    render(
      <GlossaryView
        apiClient={api}
        token="synthetic-token"
        documentId="doc-a"
        documentIds={['doc-b', 'doc-a']}
        perfil="principiante"
        idiomaSalida="es"
        idiomaUi="es"
        onSessionInvalid={vi.fn()}
      />
    );
    fireEvent.click(screen.getByRole('button', { name: t('glosario.generar', 'es') }));
    await screen.findByText('Definición multidocumental');
    expect(api.createGlossary).toHaveBeenCalledWith(
      'synthetic-token',
      expect.objectContaining({
        document_id: 'doc-a',
        document_ids: ['doc-a', 'doc-b'],
      }),
      expect.any(String)
    );
  });
});
