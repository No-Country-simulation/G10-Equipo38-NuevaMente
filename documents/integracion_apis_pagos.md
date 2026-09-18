# Guía técnica: Integración de APIs de pagos

> **Documento demo — contenido público/simulado.** Guía de referencia para equipos que integran una pasarela de pagos genérica. Todos los proveedores, dominios, claves y montos son ficticios; cualquier parecido con un proveedor real es coincidencia. No contiene datos personales reales.
> **Proyecto**: NuevaMente · **Versión**: v1 · **Nicho**: Fintech

---

## 1. Alcance y audiencia

Esta guía cubre el ciclo completo de integración de una API de pagos orientada a comercio electrónico: autenticación, creación de cobros, confirmación por webhooks, manejo de errores, reintentos y conciliación. Está pensada para desarrolladores backend que integran por primera vez una pasarela y para tech leads que definen los criterios de aceptación de la integración.

La integración de referencia asume una arquitectura de tres piezas: el frontend del comercio (que nunca toca credenciales), el backend del comercio (que firma las peticiones) y la pasarela (que procesa el cobro y notifica el resultado).

## 2. Modelo de seguridad

| Pieza | Qué posee | Qué nunca debe poseer |
|---|---|---|
| Frontend del comercio | Clave pública (`pk_sim_...`) | Clave secreta, datos de tarjeta completos |
| Backend del comercio | Clave secreta (`sk_sim_...`) | Datos de tarjeta completos (usa tokens) |
| Pasarela | Tarjetas tokenizadas | Clave secreta del comercio |

Principios no negociables:

1. La clave secreta vive solo en variables de entorno del backend; jamás en el repositorio ni en logs.
2. El navegador del cliente nunca envía datos de tarjeta al backend del comercio: los tokeniza la pasarela y el backend opera con el token.
3. Toda notificación de resultado se verifica por firma, no por confianza en el contenido recibido.

## 3. Autenticación

Cada petición incluye dos cabeceras: la clave pública que identifica al comercio y una firma HMAC-SHA256 del cuerpo con la clave secreta. La firma cubre método, ruta, timestamp y cuerpo, lo que impide reinyecciones viejas.

```text
X-Api-Key: pk_sim_7f3a...
X-Signature: hmac_sha256(secret, "POST|/v1/charges|1718000000|{...cuerpo...}")
X-Timestamp: 1718000000
```

Regla práctica: si el timestamp del cliente difiere más de 5 minutos del servidor, la pasarela rechaza con `401 TIMESTAMP_OUT_OF_RANGE`. Sincronizar relojes (NTP) es parte de la integración, no un extra.

## 4. Creación de un cobro

Endpoint principal: `POST /v1/charges`. El comercio envía monto, moneda, token de pago y una clave de idempotencia.

```python
import hashlib
import hmac
import json
import time


def crear_cargo(secret: str, public_key: str, base_url: str, cargo: dict) -> dict:
    """Crea un cobro en la pasarela demo con firma HMAC.

    `cargo` ejemplo:
    {"monto_minor": 150000, "moneda": "ARS", "token": "tok_sim_9a2b",
     "descripcion": "Orden 1024", "idempotency_key": "orden-1024-intento-1"}
    """
    cuerpo = json.dumps(cargo, separators=(",", ":"), sort_keys=True)
    timestamp = str(int(time.time()))
    firma = hmac.new(
        secret.encode(),
        f"POST|/v1/charges|{timestamp}|{cuerpo}".encode(),
        hashlib.sha256,
    ).hexdigest()
    # En producción: usar la librería HTTP del stack con las cabeceras aquí
    # calculadas (X-Api-Key, X-Signature, X-Timestamp) y body=cuerpo.
    return {"metodo": "POST", "url": base_url + "/v1/charges", "firma": firma}
```

Respuesta exitosa (`201 Created`):

```json
{
  "id": "chrg_sim_01F8A2",
  "estado": "pendiente",
  "monto_minor": 150000,
  "moneda": "ARS",
  "webhook_url": "https://comercio-demo.example/api/webhooks/pagos"
}
```

Convención de montos: `monto_minor` expresa el valor en unidades mínimas (centavos) como entero. Nunca se usa punto flotante para dinero: `0.1 + 0.2 != 0.3` y la diferencia se acumula por cobro.

## 5. Idempotencia

Los cobros envían `idempotency_key`. La semántica es exacta:

| Escenario | Resultado |
|---|---|
| Misma clave + mismo cuerpo (reintento de red) | Se devuelve la respuesta original; no se cobra dos veces |
| Misma clave + cuerpo distinto | `409 IDEMPOTENCY_CONFLICT` |
| Clave nueva | Cobro nuevo |

La clave debe derivar de la intención de negocio (p. ej. `orden-1024-intento-1`), no de un contador aleatorio por reintento: un reintento de red conserva la clave; un usuario que vuelve a intentar la compra genera una nueva.

## 6. Webhooks: la fuente de verdad del resultado

El `201` del cobro NO confirma el pago: confirma que el cobro fue aceptado para procesamiento. El resultado llega por webhook a la URL registrada.

Estructura del evento `charge.completed`:

```json
{
  "tipo": "charge.completed",
  "cargo_id": "chrg_sim_01F8A2",
  "estado": "aprobado",
  "monto_minor": 150000,
  "moneda": "ARS",
  "autorizacion": "auth_sim_5531",
  "creado_en": "2026-03-14T15:04:05Z"
}
```

Verificación obligatoria del webhook:

1. Calcular HMAC-SHA256 de la concatenación `timestamp + "." + cuerpo_raw` con el secreto de webhooks (distinto del secreto de API).
2. Comparar con la cabecera `X-Webhook-Signature` en tiempo constante (`hmac.compare_digest`).
3. Rechazar si el timestamp tiene más de 10 minutos (protección contra repetición).
4. Solo después de verificar, mutar el estado de la orden.

```python
import hmac
import hashlib


def webhook_valido(secreto_webhooks: str, timestamp: str, cuerpo_raw: bytes, firma_recibida: str) -> bool:
    """Valida firma y frescura de un webhook de la pasarela demo."""
    firma_esperada = hmac.new(
        secreto_webhooks.encode(),
        (timestamp + ".").encode() + cuerpo_raw,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(firma_esperada, firma_recibida)
```

Regla de arquitectura: el endpoint del webhook responde `200` rápido (solo verifica, persiste y encola) y el procesamiento pesado ocurre asíncrono. La pasarela reintenta si no recibe `2xx`.

## 7. Errores y reintentos

Tabla de errores estables de la pasarela:

| HTTP | `codigo` | Significado | Acción sugerida |
|---|---|---|---|
| 400 | `INVALID_REQUEST` | Cuerpo mal formado | Corregir y reenviar (nueva idempotency solo si cambió el cuerpo) |
| 401 | `AUTH_INVALID` | Firma o clave inválida | Revisar secreto y sincronía de reloj; no reintentar en bucle |
| 402 | `CARD_DECLINED` | Emisor rechazó el cobro | Informar al usuario; no reintentar automáticamente |
| 409 | `IDEMPOTENCY_CONFLICT` | Clave reutilizada con otro cuerpo | Revisar generación de claves |
| 409 | `INVALID_STATE` | El cargo ya está en estado terminal | Consultar estado en vez de reintentar |
| 429 | `RATE_LIMITED` | Límite de peticiones | Respetar `Retry-After` y aplicar backoff |
| 5xx | `PROVIDER_ERROR` | Falla transitoria de la pasarela | Reintentar con backoff + jitter (misma idempotency) |

Política de reintentos recomendada: hasta 3 intentos con espera exponencial (1 s, 2 s, 4 s) más jitter aleatorio de ±25%, siempre conservando la `idempotency_key` original. Los `4xx` distintos de `429` no se reintentan: son errores del integrador.

## 8. Conciliación diaria

El comercio debe descargar el reporte de liquidación (`GET /v1/settlements?fecha=YYYY-MM-DD`) y compararlo contra sus órdenes. Tres diferencias típicas y su tratamiento:

1. **Cobro aprobado sin orden local**: el webhook se perdió. Recuperar con `GET /v1/charges/{id}` y aplicar el mismo flujo que el webhook (verificando autenticidad de la consulta).
2. **Orden local aprobada sin cobro en la pasarela**: la orden quedó adelantada; revertir y notificar.
3. **Montos distintos**: bug de redondeo o moneda; con `monto_minor` entero esto solo ocurre si se mezclaron monedas.

## 9. Checklist de puesta en producción

- [ ] Claves solo en variables de entorno; el repositorio contiene únicas claves de sandbox.
- [ ] Webhook firma verificado en tiempo constante y con ventana de timestamp.
- [ ] Idempotencia derivada de la intención de negocio y persistida antes del primer envío.
- [ ] Montos enteros (`monto_minor`) en todo el stack, sin punto flotante.
- [ ] Backoff con jitter y respeto de `Retry-After`.
- [ ] Conciliación diaria automatizada con alertas por divergencia.
- [ ] Métricas separadas de latencia de autorización vs. notificación webhook.
- [ ] Plan de rollback documentado para mantener órdenes consistentes si la pasarela se degrada.

## 10. Glosario breve

| Término | Significado |
|---|---|
| Tokenización | Reemplazo del dato sensible de tarjeta por un identificador inerte de un uso o de corto alcance |
| Webhook | Notificación HTTP saliente de la pasarela hacia el comercio cuando algo cambia |
| Idempotencia | Garantía de que reintentar la misma operación no duplica el efecto |
| HMAC | Código de autenticación de mensaje con clave: prueba que el emisor posee el secreto sin enviarlo |
| `monto_minor` | Monto en unidades mínimas de la moneda (centavos), como entero |
| Conciliación | Comparación periódica entre los registros del comercio y los de la pasarela |
