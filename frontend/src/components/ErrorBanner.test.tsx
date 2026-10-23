import React from 'react';
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { APIError } from '../api/client';
import { ErrorBanner, mensajeError } from './ErrorBanner';

describe('ErrorBanner e i18n de límites dinámicos', () => {
  it('renderiza el límite efectivo de documentos en ES, EN y PT', () => {
    const errorDocs = new APIError('RATE_LIMITED', 'Limite', 429, 'req_123', {
      motivo: 'workspace_resource_limit',
      recurso: 'documents',
      limite: 2,
      usados: 2,
    });

    expect(mensajeError(errorDocs, 'es')).toBe(
      'Alcanzaste el límite de 2 documentos conservados. Borrá un documento para continuar.'
    );
    expect(mensajeError(errorDocs, 'en')).toBe(
      'You reached the limit of 2 retained documents. Delete a document to continue.'
    );
    expect(mensajeError(errorDocs, 'pt')).toBe(
      'Você atingiu o limite de 2 documentos mantidos. Exclua um documento para continuar.'
    );
  });

  it('renderiza el límite efectivo de generaciones en ES, EN y PT', () => {
    const errorGen = new APIError('RATE_LIMITED', 'Limite', 429, 'req_456', {
      motivo: 'workspace_resource_limit',
      recurso: 'generations',
      limite: 10,
      usados: 10,
    });

    expect(mensajeError(errorGen, 'es')).toBe(
      'Alcanzaste el límite de 10 generaciones conservadas. Borrá una generación para continuar.'
    );
    expect(mensajeError(errorGen, 'en')).toBe(
      'You reached the limit of 10 retained generations. Delete a generation to continue.'
    );
    expect(mensajeError(errorGen, 'pt')).toBe(
      'Você atingiu o limite de 10 gerações mantidas. Exclua uma geração para continuar.'
    );
  });

  it('renderiza mensaje genérico sin cifras fijas cuando no hay detalles de límite', () => {
    const errorSinDetalles = new APIError('RATE_LIMITED', 'Limite', 429, 'req_789', {
      motivo: 'workspace_resource_limit',
    });

    expect(mensajeError(errorSinDetalles, 'es')).toBe(
      'Alcanzaste el límite de recursos conservados. Borrá un recurso para continuar.'
    );
    expect(mensajeError(errorSinDetalles, 'en')).toBe(
      'You reached the limit of retained resources. Delete a resource to continue.'
    );
    expect(mensajeError(errorSinDetalles, 'pt')).toBe(
      'Você atingiu o limite de recursos mantidos. Exclua um recurso para continuar.'
    );
  });

  it('renderiza el ErrorBanner en el DOM con el límite efectivo', () => {
    const errorDocs = new APIError('RATE_LIMITED', 'Limite', 429, 'req_dom_1', {
      motivo: 'workspace_resource_limit',
      recurso: 'documents',
      limite: 3,
      usados: 3,
    });

    render(<ErrorBanner error={errorDocs} idioma="es" />);
    expect(
      screen.getByText(/Alcanzaste el límite de 3 documentos conservados/i)
    ).toBeTruthy();
  });
});
