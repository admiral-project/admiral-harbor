# PayPal Setup — Admiral Harbor

Guía completa para configurar, probar y activar en producción la integración de PayPal en
Admiral Harbor. Cubre los tres modos de operación: **mock** (desarrollo), **sandbox**
(pruebas contra API real) y **live** (producción).

---

## Tabla de contenidos

1. [Arquitectura de la integración](#1-arquitectura-de-la-integración)
2. [Variables de entorno](#2-variables-de-entorno)
3. [Prioridad de configuración](#3-prioridad-de-configuración)
4. [Modos de operación](#4-modos-de-operación)
5. [Configurar PayPal Developer Console](#5-configurar-paypal-developer-console)
6. [Configurar productos y planes de suscripción](#6-configurar-productos-y-planes-de-suscripción)
7. [Configurar el webhook](#7-configurar-el-webhook)
8. [Activar credenciales en Harbor](#8-activar-credenciales-en-harbor)
9. [Asociar Plan IDs a los tiers de aplicación](#9-asociar-plan-ids-a-los-tiers-de-aplicación)
10. [Flujo de checkout completo](#10-flujo-de-checkout-completo)
11. [Eventos de webhook manejados](#11-eventos-de-webhook-manejados)
12. [Worker de reconciliación](#12-worker-de-reconciliación)
13. [Modelo de datos](#13-modelo-de-datos)
14. [Solución de problemas](#14-solución-de-problemas)

---

## 1. Arquitectura de la integración

```
Cliente (navegador)
    │
    ▼
Harbor Portal  ──► PayPal REST API v1
    │                (OAuth2 + Billing Subscriptions)
    │
    ├── POST /billing/webhooks/paypal  ◄── PayPal Webhooks
    │       │
    │       ▼
    │   Provisioning via Admirald API
    │
    └── worker.py (reconciliación periódica)
            │
            └── GET /v1/billing/subscriptions/{id}
```

La integración usa la **PayPal REST API v1** directamente (sin SDK oficial) a través del
módulo `app/paypal.py`. No hay dependencia de `paypalrestsdk` ni de
`paypalcheckoutsdk` — todas las llamadas HTTP se realizan con `requests`.

**Archivos clave:**

| Archivo | Rol |
|---|---|
| `app/paypal.py` | Cliente HTTP PayPal: OAuth2, suscripciones, verificación de firma |
| `app/config.py` | Declaración de variables de entorno |
| `app/models.py` | `HarborPayPalConfig`, `Subscription`, `Order`, `Invoice`, `BillingEvent`, `CatalogAppTier` |
| `app/client.py` | Rutas del portal de cliente: iniciar checkout, retorno, cancelación |
| `app/portal.py` | Endpoint webhook + mock PayPal para desarrollo |
| `app/admin.py` | Rutas admin: configurar credenciales, asignar Plan ID a tiers |
| `worker.py` | Reconciliación periódica de estado de suscripciones |

---

## 2. Variables de entorno

Todas las variables se definen en `app/config.py`. Los valores por defecto están pensados
para desarrollo local con modo mock.

| Variable | Requerida | Default | Descripción |
|---|---|---|---|
| `HARBOR_PAYPAL_MODE` | Sí | `mock` | Modo de operación: `mock`, `sandbox` o `live` |
| `HARBOR_PAYPAL_CLIENT_ID` | Sandbox/Live | `""` | Client ID de la app PayPal |
| `HARBOR_PAYPAL_CLIENT_SECRET` | Sandbox/Live | `""` | Client Secret de la app PayPal |
| `HARBOR_PAYPAL_WEBHOOK_ID` | Sandbox/Live | `""` | ID del webhook registrado en PayPal |
| `HARBOR_PAYPAL_BASE_URL` | No | `https://api-m.sandbox.paypal.com` | URL base de la API PayPal (sandbox o live) |
| `HARBOR_PAYPAL_RETURN_URL` | No | `https://localhost:5000/billing/return` | Variable declarada en config; el checkout actual genera la URL de retorno dinámicamente con `url_for(..., _external=True)` |
| `HARBOR_PAYPAL_CANCEL_URL` | No | `https://localhost:5000/billing/cancel` | Variable declarada en config; el checkout actual genera la URL de cancelación dinámicamente con `url_for(..., _external=True)` |

**Para producción (live):**

```bash
HARBOR_PAYPAL_MODE=live
HARBOR_PAYPAL_CLIENT_ID=AaBb...
HARBOR_PAYPAL_CLIENT_SECRET=EeFf...
HARBOR_PAYPAL_WEBHOOK_ID=1A2B3C...
HARBOR_PAYPAL_BASE_URL=https://api-m.paypal.com
# Actualmente Harbor genera estas URLs dinámicamente en cada checkout.
# Puedes mantener estas variables documentadas, pero no controlan el flujo actual.
HARBOR_PAYPAL_RETURN_URL=https://portal.miagencia.com/billing/return
HARBOR_PAYPAL_CANCEL_URL=https://portal.miagencia.com/billing/cancel
```

---

## 3. Prioridad de configuración

La configuración se resuelve en este orden (mayor prioridad primero):

```
1. Base de datos (tabla HarborPayPalConfig)  ←  modificable desde el panel admin
2. Variables de entorno (HARBOR_PAYPAL_*)
```

Cuando el campo `mode` en la tabla `HarborPayPalConfig` es distinto de `"mock"`, Harbor
usa los valores almacenados en BD (con el `client_secret` almacenado cifrado usando
`HARBOR_ENCRYPTION_KEY`). De lo contrario, cae a las variables de entorno.

**Implicación:** si configuras via panel admin (sandbox → live), no necesitas reiniciar
Harbor. El cambio toma efecto de inmediato en la siguiente solicitud.

**Nota:** `HARBOR_PAYPAL_BASE_URL` se lee solo desde variables de entorno. El panel admin
no permite cambiarlo.

---

## 4. Modos de operación

### `mock` (desarrollo local)

- No realiza ninguna llamada real a PayPal.
- Genera `subscription_id` ficticios con prefijo `MOCK-SUB-`.
- Expone un endpoint `/mock-paypal/approve` que simula la aprobación del cliente.
- La verificación de firma del webhook siempre devuelve `True`.
- **No requiere credenciales PayPal.**

Útil para desarrollar y probar el flujo completo de checkout sin cuenta PayPal.

### `sandbox` (pruebas con API real)

- Usa `https://api-m.sandbox.paypal.com`.
- Requiere una app creada en [developer.paypal.com](https://developer.paypal.com) con
  cuentas sandbox de comprador y vendedor.
- Los webhooks deben apuntar a una URL pública (usa `ngrok` o similar en desarrollo).
- Los planes de suscripción se crean en el entorno sandbox de PayPal.

### `live` (producción)

- Usa `https://api-m.paypal.com`.
- Requiere cuenta PayPal Business verificada.
- Los planes de suscripción son distintos a los de sandbox — deben recrearse.
- Cambiar `HARBOR_PAYPAL_BASE_URL` a `https://api-m.paypal.com`.

---

## 5. Configurar PayPal Developer Console

### 5.1 Crear una app REST API

1. Ir a [developer.paypal.com](https://developer.paypal.com) → **My Apps & Credentials**.
2. Seleccionar el entorno: **Sandbox** o **Live**.
3. Hacer clic en **Create App**.
   - Nombre: p.ej. `Admiral Harbor`
   - Tipo: **Merchant**
4. Copiar **Client ID** y **Secret**.
5. En la configuración de la app, activar los permisos:
   - `Billing Agreements / Subscriptions` — requerido
   - `Transaction Search` — opcional, útil para auditoría

### 5.2 Cuentas sandbox (solo modo sandbox)

PayPal crea automáticamente cuentas sandbox al crear una app. Para simular pagos:

1. En Developer Console → **Sandbox** → **Accounts**.
2. Usar la cuenta `Personal` (comprador) para aprobar suscripciones de prueba.
3. Usar la cuenta `Business` (vendedor) para verificar que los cobros llegan.

---

## 6. Configurar productos y planes de suscripción

Cada tier de aplicación (`CatalogAppTier`) necesita un **PayPal Plan ID** asociado.
Los planes se crean en la API o en el Dashboard de PayPal.

### 6.1 Crear un producto (Catalog Product)

```bash
curl -X POST https://api-m.sandbox.paypal.com/v1/catalogs/products \
  -H "Authorization: Bearer <ACCESS_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Wiki App - Starter",
    "type": "SERVICE",
    "category": "SOFTWARE"
  }'
# Guardar el product_id devuelto
```

### 6.2 Crear un plan de suscripción

```bash
curl -X POST https://api-m.sandbox.paypal.com/v1/billing/plans \
  -H "Authorization: Bearer <ACCESS_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "product_id": "<PRODUCT_ID>",
    "name": "Wiki App - Starter Monthly",
    "status": "ACTIVE",
    "billing_cycles": [
      {
        "frequency": { "interval_unit": "MONTH", "interval_count": 1 },
        "tenure_type": "REGULAR",
        "sequence": 1,
        "total_cycles": 0,
        "pricing_scheme": {
          "fixed_price": { "value": "29.00", "currency_code": "USD" }
        }
      }
    ],
    "payment_preferences": {
      "auto_bill_outstanding": true,
      "setup_fee_failure_action": "CONTINUE",
      "payment_failure_threshold": 2
    }
  }'
# El campo "id" del response es el Plan ID — guardarlo para el paso 9
```

> Repetir este proceso para cada tier de cada aplicación. Los Plan IDs de sandbox y live
> son distintos e intercambiables, por lo que deberás crearlos en ambos entornos.

---

## 7. Configurar el webhook

Harbor expone el webhook en:

```
POST /billing/webhooks/paypal
```

### 7.1 Registrar el webhook en PayPal

1. En Developer Console → tu app → **Webhooks** → **Add Webhook**.
2. URL: `https://portal.miagencia.com/billing/webhooks/paypal`
3. Activar los siguientes eventos:

| Evento PayPal | Efecto en Harbor |
|---|---|
| `BILLING.SUBSCRIPTION.ACTIVATED` | Suscripción → `active`, provisiona instancia |
| `BILLING.SUBSCRIPTION.SUSPENDED` | Suscripción → `past_due` |
| `BILLING.SUBSCRIPTION.CANCELLED` | Suscripción → `cancelled` |
| `BILLING.SUBSCRIPTION.EXPIRED` | Suscripción → `cancelled` |
| `PAYMENT.SALE.COMPLETED` | Suscripción → `active`, genera `Invoice` |
| `PAYMENT.SALE.DENIED` | Suscripción → `past_due` |
| `PAYMENT.SALE.REFUNDED` | Suscripción → `past_due` |
| `PAYMENT.SALE.REVERSED` | Suscripción → `past_due` |
| `CUSTOMER.DISPUTE.CREATED` | Suscripción → `past_due` |

4. Copiar el **Webhook ID** generado (se usa en `HARBOR_PAYPAL_WEBHOOK_ID`).

### 7.2 Verificación de firma

Harbor verifica cada webhook llamando a:

```
POST /v1/notifications/verify-webhook-signature
```

usando los headers `PAYPAL-TRANSMISSION-ID`, `PAYPAL-TRANSMISSION-SIG`,
`PAYPAL-CERT-URL`, `PAYPAL-AUTH-ALGO` y `PAYPAL-TRANSMISSION-TIME`. Si la verificación
falla, devuelve `403`. **Si `HARBOR_PAYPAL_WEBHOOK_ID` está vacío, la verificación
siempre falla** (excepto en modo `mock`).

### 7.3 Pruebas locales con ngrok (sandbox)

```bash
ngrok http 5000
# Copiar la URL pública (e.g. https://abc123.ngrok.io)
# Registrar https://abc123.ngrok.io/billing/webhooks/paypal en Developer Console
```

---

## 8. Activar credenciales en Harbor

### Opción A — Panel admin (recomendado)

1. Iniciar sesión como administrador en Harbor.
2. Ir a **Administración** → **Configuración PayPal**.
3. Introducir:
   - **Mode**: `sandbox` o `live`
   - **Client ID** y **Client Secret**
   - **Webhook ID**
4. Guardar. El `client_secret` se almacena cifrado usando `HARBOR_ENCRYPTION_KEY`.

**Comportamiento actual del formulario:** el backend requiere reenviar `Client Secret`
en cada guardado. Dejarlo vacío no conserva automáticamente el valor previo.

> `HARBOR_ENCRYPTION_KEY` debe estar configurado antes de guardar credenciales via admin.
> Si no está configurado, Harbor arranca pero el cifrado no funciona correctamente.

### Opción B — Variables de entorno

Establecer las variables listadas en la sección 2 y reiniciar Harbor. Las variables de
entorno son usadas como fallback cuando la tabla `HarborPayPalConfig` está en modo `mock`
o vacía.

---

## 9. Asociar Plan IDs a los tiers de aplicación

Cada tier de aplicación del catálogo debe tener un `paypal_plan_id` configurado para que
los clientes puedan suscribirse.

1. Ir a **Administración** → **Catálogo de aplicaciones** → seleccionar una app.
2. Para cada tier, introducir el **Plan ID** obtenido en el paso 6.
3. Guardar.

El campo `paypal_plan_id` se almacena en `CatalogAppTier`. Si un tier no tiene Plan ID
configurado y el modo no es `mock`, Harbor bloquea el checkout antes de llamar a PayPal y
muestra el mensaje `PayPal plan is not configured for this tier.`.

---

## 10. Flujo de checkout completo

```
Cliente selecciona plan
        │
        ▼
POST /deploy  →  paypal.create_subscription(plan_id, return_url, cancel_url)
                        │
                        ▼
              PayPal devuelve approval_url
                        │
                        ▼
       Cliente redirigido a PayPal para aprobar
                        │
              ┌─────────┴─────────┐
              │                   │
           Aprueba             Cancela
              │                   │
              ▼                   ▼
     GET /billing/return    GET /billing/cancel
              │
              ▼ valida `token` + consulta `GET /v1/billing/subscriptions/{id}`
              ▼
     si estado = ACTIVE o APPROVED:
       - mock: provisiona inmediatamente
       - sandbox/live: marca la orden `approved` y espera webhook
              │
              ▼
 POST /billing/webhooks/paypal
   evento: BILLING.SUBSCRIPTION.ACTIVATED o PAYMENT.SALE.COMPLETED
              │
              ▼
   Provisiona instancia en Admirald si aún no existe
   PAYMENT.SALE.COMPLETED genera Invoice y Payment
```

En **modo mock**, `/billing/return` consulta la suscripción con `get_subscription()` y,
si el estado es `ACTIVE` o `APPROVED`, aprovisiona directamente sin esperar webhook. En
**sandbox/live**, ese mismo retorno marca la orden como `approved` y muestra
`Waiting for webhook confirmation`; la provisión real ocurre cuando llega
`BILLING.SUBSCRIPTION.ACTIVATED` o `PAYMENT.SALE.COMPLETED`.

---

## 11. Eventos de webhook manejados

El endpoint `POST /billing/webhooks/paypal` aplica idempotencia por `event_id`: si el
evento ya existe en `BillingEvent`, devuelve `200 {"status": "duplicate"}` sin procesarlo
de nuevo.

| Evento | Nuevo estado `Subscription.status` | Acción adicional |
|---|---|---|
| `BILLING.SUBSCRIPTION.ACTIVATED` | `active` | Provisiona instancia si no existe |
| `PAYMENT.SALE.COMPLETED` | `active` | Provisiona instancia si no existe + crea `Invoice` |
| `PAYMENT.SALE.DENIED` | `past_due` | — |
| `BILLING.SUBSCRIPTION.SUSPENDED` | `past_due` | — |
| `BILLING.SUBSCRIPTION.CANCELLED` | `cancelled` | — |
| `BILLING.SUBSCRIPTION.EXPIRED` | `cancelled` | — |
| `PAYMENT.SALE.REFUNDED` | `past_due` | — |
| `PAYMENT.SALE.REVERSED` | `past_due` | — |
| `CUSTOMER.DISPUTE.CREATED` | `past_due` | — |

Si la provisión falla (error en Admirald API), `Subscription.status` se marca como
`suspended` y el evento se registra en `BillingEvent` con `status="failed_provision"`.
El webhook devuelve `502`.

---

## 12. Worker de reconciliación

`worker.py` incluye `_reconcile_paypal_subscriptions()` que se ejecuta periódicamente
(cada ciclo del worker). Para cada suscripción con estado `active` o `past_due` que tenga
un `paypal_subscription_id` real:

1. Llama a `GET /v1/billing/subscriptions/{id}` en PayPal.
2. Reconcilia el estado remoto con el local:

| Estado PayPal | Estado local | Acción |
|---|---|---|
| `SUSPENDED` | `active` | → `past_due` |
| `CANCELLED` | cualquiera ≠ `cancelled` | → `cancelled` |
| `ACTIVE` o `APPROVED` | `past_due` | → `active` |

Esto garantiza que cancelaciones o suspensiones realizadas directamente en el panel de
PayPal (fuera de webhooks) se reflejen en Harbor. Las suscripciones de test
(`is_test_app=True`) quedan excluidas de la reconciliación.

---

## 13. Modelo de datos

| Tabla / Modelo | Campos PayPal relevantes |
|---|---|
| `HarborPayPalConfig` | `mode`, `client_id`, `client_secret` (cifrado), `webhook_id` |
| `CatalogAppTier` | `paypal_plan_id` |
| `Subscription` | `paypal_subscription_id`, `paypal_plan_id` |
| `Order` | `paypal_subscription_id`, `paypal_plan_id` |
| `Invoice` | `paypal_transaction_id`, `paypal_event_id` |
| `BillingEvent` | `event_id`, `event_type`, `status`, `payload_json` (audit log) |

`HarborPayPalConfig` es un singleton — solo existe una fila. `BillingEvent` actúa como
log de auditoría de todos los webhooks recibidos, incluyendo los fallidos.

---

## 14. Solución de problemas

### Webhook devuelve 403

- Verificar que `HARBOR_PAYPAL_WEBHOOK_ID` es correcto y coincide con el ID registrado
  en Developer Console.
- Verificar que `HARBOR_PAYPAL_CLIENT_ID` y `HARBOR_PAYPAL_CLIENT_SECRET` permiten
  obtener un access token (ver logs de Harbor).
- En modo sandbox, asegurarse de que la URL del webhook es alcanzable públicamente
  (no `localhost`).

### El cliente no es redirigido a PayPal

- El tier no tiene `paypal_plan_id` configurado. Ver sección 9.
- Harbor está en modo `mock`; en ese caso la aprobación usa `/mock-paypal/approve` en vez
  de una página real de PayPal. Verificar `HARBOR_PAYPAL_MODE`.

### La instancia no se provisiona tras el pago

- Revisar `BillingEvent` en el panel admin para ver si llegó el webhook
  `BILLING.SUBSCRIPTION.ACTIVATED` o `PAYMENT.SALE.COMPLETED`.
- Si el evento llegó pero la provisión falló (`status="failed_provision"`), revisar
  la conectividad entre Harbor y Admirald (`ADMIRAL_API_URL`, `ADMIRAL_ADMIN_TOKEN`,
  `ADMIRAL_CA_FILE`).
- En modo sandbox, verificar que el Plan ID existe y está en estado `ACTIVE` en PayPal.

### `client_secret` no se descifra

- Verificar que `HARBOR_ENCRYPTION_KEY` es la misma que cuando se guardaron las
  credenciales. Si cambió, las credenciales deben reingresarse desde el panel admin.

### Cambiar de sandbox a live

1. Crear productos y planes en el entorno **live** de PayPal (paso 6).
2. Registrar un webhook nuevo apuntando al dominio de producción (paso 7).
3. Actualizar el panel admin con las credenciales live y el nuevo Webhook ID (paso 8).
4. Actualizar los `paypal_plan_id` en cada tier con los Plan IDs live (paso 9).
5. Cambiar `HARBOR_PAYPAL_BASE_URL` a `https://api-m.paypal.com` en las variables
   de entorno. Ese campo solo se lee desde env, no desde la BD ni desde el panel admin.

> Los Plan IDs de sandbox y live son distintos. Las suscripciones existentes en sandbox
> no migran a live — solo aplica a suscripciones nuevas.
