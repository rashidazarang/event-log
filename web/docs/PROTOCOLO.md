# Protocolo de monitores del registro

Un monitor es cualquier proceso que se inscribe con un **filtro** y unas
**capacidades**, y a partir de ahí observa el log, reacciona y —si lo declaró—
actúa sobre el trabajo. El arnés lo construye quien quiera: aquí está el
contrato completo. `monitor.sh` en la raíz es una implementación de referencia
en bash; no es la única forma correcta, es la prueba de que el contrato basta.

Base: `http://127.0.0.1:8124` en local. Todo es JSON.

---

## 1. Los tres conceptos

**La clave de génesis.** `ev-20260910-a3f91c`. Nace con una entrada y no cambia
nunca: ni al renombrarla, ni al moverla de espacio, ni al cerrarla. Es lo que
permite que la misma cosa exista en Event Log, en un repo y en la nube sin
duplicarse. Un monitor guarda claves de génesis, nunca títulos ni posiciones.

**El cursor.** El log de eventos tiene un `seq` entero y monotónico. Un monitor
guarda un solo número y pide lo que pasó después. Con fechas, dos eventos del
mismo segundo se pierden o se repiten; con un entero, no.

El `head` que devuelve el feed es **el máximo real del log**, no el del tramo
que pasó tu filtro. Si fuera lo segundo, un filtro estrecho dejaría al monitor
anclado en el último evento que le interesó, y su atraso crecería para siempre.

**Las capacidades.** `observar` lee. `reaccionar` comenta y etiqueta. `actuar`
reclama con arrendamiento, reporta avance y cierra. Lo que no se declara, el bus
lo rechaza con `403`: la capacidad es un contrato, no una sugerencia.

---

## 2. Inscribirse

```http
POST /api/monitors
Content-Type: application/json

{
  "key": "mi-monitor",
  "name": "Para qué sirve",
  "kind": "shell",
  "filter": { "space": ["sonda"], "event": ["entrada.*"], "abiertos": true },
  "capabilities": ["observar", "reaccionar", "actuar"],
  "lease_seconds": 900,
  "desde": "ahora"
}
```

```json
{ "key": "mi-monitor", "token": "…", "cursor": 939, "head": 939,
  "capacidades": ["observar","reaccionar","actuar"], "filtro": { },
  "aviso": "El token se muestra una sola vez. Guárdalo." }
```

`desde: "ahora"` arranca en la cabeza del log; omitirlo arranca en 0 y entrega
todo el historial que pase el filtro. Volver a llamar con la misma clave
**renueva** el monitor y emite un token nuevo; el cursor se conserva.

El token va en cada llamada:

```
Authorization: Bearer <token>
```

Se guarda hasheado. Si se pierde, se re-inscribe la misma clave.

---

## 3. El filtro

La misma gramática que usa la interfaz. Entre claves es **Y**; dentro de una
clave es **O**. Lo que no se nombra, no se filtra.

| Clave | Contra qué compara | Ejemplo |
|---|---|---|
| `event` | el tipo de evento; `*` funciona de sufijo | `["entrada.*", "senal.ingerida"]` |
| `space` | el espacio | `["sonda", "correo"]` |
| `actor` | quién lo provocó | `["agente-ejecutor"]` |
| `kind` | tipo de entrada | `["ticket"]` |
| `status` | estado | `["entrante", "bloqueado"]` |
| `priority` | prioridad | `["P0", "P1"]` |
| `assignee` / `requester` | responsable / solicitante | `["dana"]` |
| `labels_any` / `labels_all` | etiquetas | `["gate", "evidencia"]` |
| `meta` | metadata del espacio | `{"banda": ["PROD"]}` |
| `domain` / `action` | señales normalizadas | `{"domain":["email"],"action":["bounced"]}` |
| `text` | subcadena en título, cuerpo, clave o metadata | `"resend"` |
| `abiertos` | descarta cerradas y descartadas | `true` |

**Una clave desconocida se rechaza con `400`.** Un filtro con un typo no filtra
nada y el monitor concluye que no hay trabajo; eso es peor que un error.

Tipos de evento: `entrada.creada`, `entrada.actualizada`, `entrada.movida`,
`entrada.cerrada`, `entrada.comentada`, `entrada.asignada`, `entrada.reclamada`,
`entrada.avance`, `entrada.liberada`, `entrada.arriendo_vencido`,
`senal.ingerida`, `fuente.registrada`, `monitor.registrado`.

---

## 4. El ciclo

```http
GET /api/monitors/mi-monitor/feed?wait=25&limit=50
Authorization: Bearer <token>
```

`wait` es espera larga en segundos (máximo 50): la petición se queda abierta
hasta que haya algo o venza. `0` responde de inmediato.

```json
{ "monitor": "mi-monitor", "cursor": 939, "head": 987, "atraso": 48,
  "siguiente": 962,
  "events": [ { "seq": 941, "kind": "entrada.creada", "at": "…",
                "genesis": "ev-20260910-5fd40b", "actor": "dana",
                "payload": { },
                "entrada": { "title": "…", "status": "entrante" } } ] }
```

El feed **no avanza el cursor**. Cuando terminaste de procesar:

```http
POST /api/monitors/mi-monitor/ack   { "cursor": 962 }
```

Ese orden —pedir, procesar, confirmar— es lo que hace que sea una cola y no una
notificación: si el proceso muere a medias, los mismos eventos se vuelven a
entregar. Un monitor debe ser **idempotente por `seq`**.

Se puede acotar más en la llamada (`?space=sonda`), nunca ampliar: los
parámetros se intersectan con el filtro inscrito.

---

## 5. Reaccionar

Requiere `reaccionar`. Comentar y etiquetar no cambian de dueño ni de prioridad,
así que no exigen `actuar`:

```http
POST /api/monitors/<clave>/annotate
{ "genesis": "ev-…", "comentario": "…", "labels_add": ["triage-auto"],
  "labels_del": [], "regla": "reactivo v1.2.0 · etiqueta:resend" }
```

`regla` es opcional y se guarda con el cambio: seis meses después la pregunta no
es qué se etiquetó, es **qué regla lo etiquetó**.

El mismo endpoint acepta `assignee` y `priority`, pero eso ya es actuar y se
exige aparte: sin `actuar` responde `403` y dice qué alcanzó a hacer antes de
frenar.

## 6. Actuar

Requiere `actuar` entre las capacidades. Todo va contra una clave de génesis.

```http
POST /api/monitors/<clave>/claim      { "genesis": "ev-…", "lease": 600, "nota": "lo tomo" }
POST /api/monitors/<clave>/progress   { "genesis": "ev-…", "nota": "…", "renovar": 600 }
POST /api/monitors/<clave>/complete   { "genesis": "ev-…", "status": "en revision", "nota": "…" }
POST /api/monitors/<clave>/release    { "genesis": "ev-…", "motivo": "…" }
POST /api/monitors/<clave>/retire     { }
```

**El arrendamiento es el corazón, y es atómico.** `claim` es un solo `UPDATE`
condicional: quien cambió la fila ganó, el resto recibe `409`. Leer y luego
escribir deja una ventana en la que dos monitores leen "libre" y los dos creen
ganar — 24 de 24 hilos ganan así en `pruebas/prueba-reclamo-atomico.py`.
`complete` está condicionado igual: si tu arrendamiento venció y otro ya
reclamó, tu cierre llega tarde y recibe `409` en vez de pisarlo.

`claim` marca la entrada con tu clave y una expiración. Si otro monitor la reclama, recibe `409` con quién la tiene y hasta
cuándo. Si tu proceso muere, la expiración la devuelve sola a la cola y emite
`entrada.arriendo_vencido` — sin eso, un monitor caído se lleva el trabajo
consigo y nadie se entera hasta que alguien pregunta. Un trabajo largo renueva
con `progress { "renovar": N }`.

`complete` cierra el arrendamiento, mueve el estado y **escribe una revisión**
con el contenido anterior completo.

### Códigos

| Código | Significa |
|---|---|
| `401` | token ausente o inválido |
| `403` | la capacidad no fue declarada al inscribirse |
| `404` | monitor o entrada inexistente |
| `409` | ya reclamada por otro, o no la tienes reclamada |
| `400` | filtro o estado inválido; el cuerpo dice cuál |

---

## 7. Un monitor completo, en cualquier lenguaje

```python
import json, os, urllib.request

BASE, KEY, TOK = "http://127.0.0.1:8124", "mi-monitor", os.environ["EVLOG_TOKEN"]

def call(method, path, body=None):
    req = urllib.request.Request(
        BASE + path, method=method,
        data=json.dumps(body).encode() if body else None,
        headers={"Authorization": f"Bearer {TOK}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.load(r)

while True:
    lote = call("GET", f"/api/monitors/{KEY}/feed?wait=25&limit=50")
    for ev in lote["events"]:
        atender(ev)                                   # idempotente por ev["seq"]
    if lote["events"]:
        call("POST", f"/api/monitors/{KEY}/ack", {"cursor": lote["siguiente"]})
```

Eso es todo el protocolo. Reclamar y cerrar son dos llamadas más.

---

## 8. Dejar trabajo desde fuera

Quien no es monitor pero quiere pedir algo usa la puerta pública. No lleva
token, siempre entra como `entrante` y siempre declara quién lo pide.

```http
POST /api/intake
{ "titulo": "…", "cuerpo": "…", "space": "mirador",
  "solicitante": "ana", "contacto": "ana@ejemplo.mx", "priority": "P1",
  "labels": ["bug"] }
```

## 9. Depositar señales

Una fuente se registra **con su contexto** —para qué sirve, quién responde por
ella, qué se hace con lo que llega— y recibe un endpoint:

```http
POST /api/hooks/<clave-de-fuente>
```

Verificación `svix` (Resend) o `hmac-sha256`. El secreto vive en el entorno como
`EVLOG_SECRET_<CLAVE>`, nunca en la base. **Falla cerrado**: sin secreto
configurado, una fuente que declaró verificación rechaza todo.

La carga cruda se guarda íntegra y **nunca se reescribe, ni cuando la firma
falla**: un rechazo también es evidencia. Encima, el normalizador traduce el
vocabulario de la fuente al del almacén y produce un hecho con su digest. Las
dos capas conviven: el crudo para auditar, el hecho para consultar.
