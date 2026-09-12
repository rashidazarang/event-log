# Event Log

**Casi todos los sistemas tratan su registro de eventos como un subproducto.** Lo
escriben para depurar, lo rotan a los siete días y toman las decisiones contra
una tabla de estado que alguien actualiza con un `UPDATE`. El registro cuenta lo
que pasó; la tabla dice lo que *es*. Cuando las dos discrepan, gana la tabla, y
nadie se entera.

Esto lo invierte. **El registro es el sistema.** Las tareas, las notas y las
señales del mundo exterior no son tres bases de datos: son tres proyecciones del
mismo log append-only. Nada cambia sin dejar un evento, y ese evento es lo
autoritativo.

Este repositorio es una implementación de referencia de esa idea, completa y
ejecutable: librería estándar de Python, SQLite, JavaScript plano. Sin
dependencias, sin build, sin framework.

**Demostración en vivo: [event-log-demo.vercel.app](https://event-log-demo.vercel.app)**
— es el servidor de este repositorio corriendo como función; escribir funciona de
verdad y el estado se recicla con la instancia.

---

## Qué cambia cuando el log manda

### 1. Suscribirse deja de ser un problema

Cada evento lleva un entero monotónico. Un proceso guarda ese número y pide lo
que vino después:

```
GET  /api/monitors/<clave>/feed?wait=25   →  eventos desde el cursor
POST /api/monitors/<clave>/ack            →  avanza el cursor
```

Eso es todo el contrato de suscripción. No hay webhooks que reintentar, ni
colas que administrar, ni "¿me llegó el mensaje?". El suscriptor pregunta desde
dónde quiere y el log responde.

Con marcas de tiempo esto no funciona: dos eventos del mismo segundo se pierden
o se repiten, y los relojes de dos máquinas nunca coinciden. Con un entero, no
hay ambigüedad posible.

**Y el detalle que decide si sirve o no:** el cursor avanza en un paso separado.
Pedir no confirma. Si el proceso muere a la mitad, los mismos eventos se vuelven
a entregar. Eso convierte una notificación en una cola.

### 2. La identidad deja de mentir

Cada cosa nace con una clave inmutable —`ev-20260912-a3f91c`— que no cambia
nunca: ni al renombrarla, ni al moverla de contexto, ni al cerrarla. Y cada
versión lleva el sha256 de su contenido canónico.

La diferencia importa más de lo que parece. Cuando un eslabón se nombra por
etiqueta en vez de por contenido, la cadena **parece** intacta y no lo está: doce
despliegues seguidos pueden declarar la misma versión equivocada porque la
etiqueta se resolvió caminando hacia atrás, y los digests —que sí eran
correctos— nadie los miraba. Una etiqueta puede mentir durante semanas. Un
digest no.

### 3. Varios programas pueden trabajar la misma cola sin coordinarse

Un suscriptor que además actúa **reclama** con arrendamiento: se marca como
dueño de una entrada hasta una hora determinada. Otro que lo intente recibe un
`409` con quién la tiene y hasta cuándo. Si el primero muere, la expiración la
devuelve sola a la cola y emite el evento correspondiente.

El reclamo es un solo `UPDATE` condicional, no un `SELECT` seguido de un
`UPDATE`. La diferencia no es teórica: `pruebas/prueba-reclamo-atomico.py`
lanza 24 hilos contra la misma entrada y, con la forma ingenua, **los 24 creen
ganar**. El registro anota; el motor excluye.

### 4. Lo que entra de afuera queda auditable

Una señal externa —un correo que rebota, una medición que falla, un webhook de
un tercero— se guarda dos veces: la carga **cruda e inmutable** tal como llegó,
y encima el hecho **normalizado** al vocabulario del sistema.

El crudo no se reescribe nunca, **ni cuando la firma falla**: un rechazo también
es evidencia. Seis meses después se puede preguntar "¿por qué el sistema creyó
esto?" y la respuesta está completa, con su firma, sus encabezados y su digest.

### 5. La historia sale gratis

Cada mutación guarda el contenido anterior completo antes de escribir el nuevo.
No hay que decidir qué auditar: el log es la auditoría, y reconstruir el estado
en cualquier punto es leerlo hasta ahí.

---

## Las tres proyecciones

Que estén juntas es el punto, no una comodidad.

**Trabajo** — tiene dueño y estado. Alguien lo pidió, alguien lo hace, se cierra.

**Conocimiento** — lo que se aprendió, no lo que se hace. Nadie lo va a "cerrar";
existe para que en seis meses no se vuelva a descubrir lo mismo.

**Señales** — el rastro de lo que ocurre afuera. Miles. No son trabajo, son
hechos.

Separadas en tres herramientas, nadie puede conectar *"este cliente se quejó"*
con *"este dominio viene rebotando desde hace una semana"*. Sobre un solo log,
esa consulta es una línea.

---

## Lo que trae el repositorio

| | |
|---|---|
| `core.py` | el log, la identidad, el digest, el evaluador de filtros |
| `server.py` | la API y la interfaz; `BaseHTTPRequestHandler`, nada más |
| `seed.py` | un escenario de demostración determinista (mismo CSV, mismo tablero) |
| `web/` | siete vistas: bandeja, tablero, notas, almacén, feed, suscriptores, fuentes |
| `monitor.sh` | suscriptor de referencia en bash — existe para probar que el contrato basta |
| `monitor-reactivo.py` | reacciona: enruta y asigna el trabajo entrante por reglas versionadas |
| `bucle-proactivo.py` | mide el estado con nueve sondas y convierte hallazgos en trabajo |
| `pruebas/` | el control positivo del reclamo atómico |
| `web/docs/PROTOCOLO.md` | el contrato completo de suscripción |
| `docs/PRACTICAS.md` | por qué está construido así, con el defecto que cazó cada regla |

```bash
python3 seed.py
python3 server.py --port 8124      # http://127.0.0.1:8124
```

Python 3.9+. No hay nada más que instalar.

---

## Los dos agentes

Ninguno vive dentro del sistema. Hablan sólo por el protocolo público, igual que
lo haría un arnés escrito por otra persona en otro lenguaje.

El **reactivo** observa el log y enruta lo que entra. Decide desde el estado
actual, no desde el evento: un evento reentregado tras una caída vuelve a leer
la entrada y concluye lo mismo. La idempotencia no se pide, se construye.

El **proactivo** no espera a que alguien note un problema. Pasa sus sondas sobre
el estado real —entradas sin dueño, bloqueadas sin destinatario, suscriptores
atrasados, fuentes que enmudecieron, rebote por dominio— y abre trabajo con la
lectura adentro. Cada hallazgo tiene nombre estable: repetirlo actualiza la
entrada que ya existe en vez de abrir la segunda, y desaparecer la cierra
citando la medición que la cerró.

Los dos anotan lo que hicieron **y lo que decidieron no hacer**, en bitácoras
append-only. Un triage que sólo registra sus aciertos no se puede auditar,
porque la pregunta interesante nunca es qué atendió: es qué dejó pasar.

---

## Extensible sin tocar el esquema

Un **espacio** es un cliente, un producto, un frente de trabajo. Cada uno declara
sus propios campos:

```json
{"campos": [
  {"clave": "banda", "etiqueta": "Banda", "tipo": "opcion",
   "opciones": ["QA", "UAT", "PROD"]}
]}
```

La interfaz los pinta sola y el filtro los entiende (`meta.banda=PROD`), tanto
desde la pantalla como desde el filtro de un suscriptor. Añadir un contexto con
su vocabulario es una fila en una tabla: no toca el esquema, ni el servidor, ni
el front.

---

## Honestidad sobre los límites

- **La sincronización sólo sube.** Hay una cola durable hacia Postgres con el
  digest como clave de idempotencia. Leer de vuelta exige decidir quién manda
  cuando ambos cambian, y esa decisión no está tomada.
- **No hay autenticación de personas.** Los suscriptores llevan token; la
  interfaz asume localhost. Exponerlo a internet pide una capa de identidad.
- **Un solo proceso escribe.** SQLite aguanta de sobra el volumen que esto
  maneja, pero el diseño no es un sistema distribuido y no pretende serlo.
- **La espera larga del feed no cabe en una función sin estado.** En un
  despliegue serverless el suscriptor sondea; en un servidor de verdad, espera.

---

## Por qué esto, y no un gestor de tickets

Un gestor de tickets está construido para que una persona lea una pantalla. Su
API es un añadido, y su registro de cambios es una pestaña.

Cuando parte del trabajo lo hacen programas, eso se rompe por el lado
equivocado. Un programa no necesita una pantalla: necesita saber qué cambió
desde la última vez que miró, poder tomar algo sin que otro lo tome también, y
dejar constancia de lo que hizo con la misma fuerza que la deja una persona.

Eso no es una integración que se le cuelga a un gestor de tickets. Es una
propiedad del sustrato, y sólo se tiene si el log manda desde el principio.

---

MIT.
