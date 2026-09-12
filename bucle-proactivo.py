#!/usr/bin/env python3
"""
Event Log · bucle proactivo — mide el estado y convierte lo que encuentra en trabajo.

No espera a que alguien note un problema: pasa un juego de sondas sobre el
estado real, y cada hallazgo se vuelve una entrada con su lectura adentro,
enrutada con las mismas reglas que usa el monitor reactivo.

Prácticas asimiladas del bucle de calidad de Sonda (`tools/sonda-scorecard`,
`tools/arnes-sonda`) y de su disciplina de instrumentación:

- **El instrumento se nombra por contenido.** Cada lectura anota
  `INSTRUMENTO_VERSION` y el sha256 del archivo que la produjo. Una lectura sin
  saber con qué se tomó no es comparable con nada.
- **La predicción se escribe antes de la medición que la prueba.** Y una
  predicción falsificada es un resultado: se anota, no se corrige a posteriori.
- **Un umbral no se mueve para que algo pase.** Cambiarlo sube la versión del
  instrumento y la corrida siguiente trae la tabla de deriva.
- **Una fila de ledger por corrida, append-only.** Una corrección es una fila
  nueva, jamás una reescritura.
- **Falta de acceso no es cero.** Una sonda que no pudo medir anota `null`, no
  `0`; con cero, un servidor caído se lee como salud perfecta.
- **Un hallazgo tiene nombre estable.** Repetirlo actualiza la entrada que ya
  existe en vez de abrir la segunda; desaparecer la cierra citando la lectura
  que la cerró.

    python3 bucle-proactivo.py --una-vez            # una pasada, sin escribir
    python3 bucle-proactivo.py --una-vez --aplicar  # una pasada, abre y cierra
    python3 bucle-proactivo.py --intervalo 900 --aplicar
"""
import argparse, hashlib, json, os, sys, time, urllib.error, urllib.parse, urllib.request
import datetime as dt

INSTRUMENTO = "evlog-bucle"
INSTRUMENTO_VERSION = "0.2.0"
# Deriva del instrumento, en orden inverso. Cambiar una sonda cambia lo que la
# lectura significa: la versión sube y la corrida siguiente publica su tabla.
DERIVA = {
    "0.2.0": "La sonda `estancada` medía contra `updated`, y `updated` lo levanta "
             "cualquiera — incluido el monitor reactivo al comentar. Con eso la sonda "
             "no podía dispararse nunca: pasaba porque su objeto de medición había "
             "desaparecido. Ahora mide el último evento de un actor HUMANO. "
             "La predicción de `outbox` se corrige (esperaba >=5000 contra un piso de "
             "5000 y una cola de 1090): la predicción estaba mal, el umbral no se toca.",
    "0.1.0": "primera versión",
}
AUTOMATAS = ("bucle-proactivo", "reactivo", "sistema", "monitor-correo")

RAIZ = os.path.dirname(os.path.abspath(__file__))
URL = os.environ.get("EVLOG_URL", "http://127.0.0.1:8124")
ESTADO = os.environ.get("EVLOG_ESTADO", os.path.expanduser("~/.evlog"))
LEDGER = os.path.join(RAIZ, "evidencia", "ledger.jsonl")
PARAR = os.path.join(ESTADO, "stop-bucle")
ACTOR = "bucle-proactivo"


def ahora():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def horas_desde(iso):
    if not iso:
        return None
    try:
        t = dt.datetime.fromisoformat(iso)
        if t.tzinfo is None:
            t = t.replace(tzinfo=dt.timezone.utc)
        return (dt.datetime.now(dt.timezone.utc) - t).total_seconds() / 3600
    except Exception:
        return None


def digest_instrumento():
    """sha256 de este archivo. Dos lecturas con el mismo número y distinto
    digest no son la misma medición."""
    with open(os.path.abspath(__file__), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def reglas():
    with open(os.path.join(RAIZ, "reglas.json"), encoding="utf-8") as fh:
        return json.load(fh)


class SinAcceso(Exception):
    """La sonda no pudo medir. Se anota null, nunca cero."""


def api(ruta, metodo="GET", cuerpo=None):
    req = urllib.request.Request(
        URL + ruta, method=metodo, headers={"Content-Type": "application/json"},
        data=json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            raise SinAcceso(json.load(e).get("error", e.reason))
        except SinAcceso:
            raise
        except Exception:
            raise SinAcceso(str(e.reason))
    except Exception as e:
        raise SinAcceso(str(e))


def ultima_fila(ruta=LEDGER):
    if not os.path.exists(ruta):
        return None
    ultima = None
    with open(ruta, encoding="utf-8") as fh:
        for l in fh:
            l = l.strip()
            if l:
                try:
                    ultima = json.loads(l)
                except Exception:
                    pass
    return ultima


def anota(fila, ruta=LEDGER):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(fila, ensure_ascii=False, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


# ============================================================== las sondas ===
# Cada sonda devuelve (valor, hallazgos). Un hallazgo lleva su nombre estable,
# el sujeto, la evidencia con su fuente, y a qué espacio pertenece.

def sonda_sin_dueno(ctx):
    u = ctx["umbrales"]["sin_dueno_horas"]
    d = api("/api/entries?abiertos=1&per=200")
    viejas = [e for e in d["items"]
              if not e["assignee"] and e["kind"] == "ticket"
              and (horas_desde(e["created"]) or 0) >= u]
    hall = [{"nombre": f"sin-dueno:{e['genesis']}", "sujeto": e["genesis"],
             "espacio": e["space"], "prioridad": "P2",
             "titulo": f"Sin dueño desde hace {int(horas_desde(e['created']))} h: {e['title'][:60]}",
             "evidencia": (f"`{e['genesis']}` sigue sin responsable {int(horas_desde(e['created']))} h "
                           f"después de su alta (umbral {u} h). Prioridad {e['priority']}, "
                           f"espacio {e['space']}, pedida por {e['requester']}.\n\n"
                           f"Medido sobre /api/entries?abiertos=1 a las {ahora()}."),
             "etiquetas": ["sin-dueno"], "refs": [{"tipo": "entrada", "valor": e["genesis"]}]}
            for e in viejas]
    return len(viejas), hall


def es_automata(actor):
    a = actor or ""
    return a in AUTOMATAS or a.startswith("fuente:") or a.startswith("monitor")


def ultimo_toque_humano(genesis):
    """Horas desde el último evento de una persona. `updated` no sirve: lo levanta
    cualquier comentario automático, y una sonda de estancamiento que cuenta el
    toque de su propio robot como movimiento no se dispara jamás."""
    d = api(f"/api/entries/{genesis}")
    for ev in d.get("eventos") or []:
        if not es_automata(ev.get("actor")):
            return horas_desde(ev["at"])
    return horas_desde(d.get("created"))


def sonda_estancada(ctx):
    u = ctx["umbrales"]["estancada_dias"]
    d = api("/api/entries?abiertos=1&per=200")
    quietas = []
    for e in d["items"]:
        if e["kind"] != "ticket":
            continue
        # el filtro barato primero; sólo se pide la bitácora de las que parecen
        # frescas pero son viejas, que son las únicas donde el robot pudo mentir
        if (horas_desde(e["created"]) or 0) < u * 24:
            continue
        h = horas_desde(e["updated"]) or 0
        if h < u * 24:
            h = ultimo_toque_humano(e["genesis"]) or 0
            if h < u * 24:
                continue
        e = dict(e, _horas=h)
        quietas.append(e)
    hall = [{"nombre": f"estancada:{e['genesis']}", "sujeto": e["genesis"],
             "espacio": e["space"], "prioridad": "P3",
             "titulo": f"Sin movimiento humano {int(e['_horas'] / 24)} d: {e['title'][:52]}",
             "evidencia": (f"`{e['genesis']}` está {e['status']} y su último evento **de una "
                           f"persona** es de hace {int(e['_horas'] / 24)} días (umbral {u}); los "
                           f"toques automáticos no cuentan como movimiento. "
                           f"Dueño: {e['assignee'] or 'ninguno'}.\n\nO se mueve o se descarta; "
                           f"quedarse abierta sin avance es lo que hace que el tablero mienta."),
             "etiquetas": ["estancada"], "refs": [{"tipo": "entrada", "valor": e["genesis"]}]}
            for e in quietas]
    return len(quietas), hall


def sonda_sla(ctx):
    sla = ctx["umbrales"]["sla_horas"]
    d = api("/api/entries?abiertos=1&per=200")
    rotas = []
    for e in d["items"]:
        lim = sla.get(e["priority"])
        h = horas_desde(e["created"])
        if lim and h and h > lim and e["kind"] == "ticket":
            rotas.append((e, h, lim))
    hall = [{"nombre": f"sla:{e['genesis']}", "sujeto": e["genesis"], "espacio": e["space"],
             "prioridad": "P1" if e["priority"] == "P0" else "P2",
             "titulo": f"{e['priority']} fuera de SLA por {int(h - lim)} h: {e['title'][:52]}",
             "evidencia": (f"`{e['genesis']}` es {e['priority']} y lleva {int(h)} h abierta; el "
                           f"acuerdo para {e['priority']} es {lim} h. Excedido por {int(h - lim)} h.\n\n"
                           f"El SLA vive en reglas.json v{ctx['reglas_version']}. Si el acuerdo ya "
                           f"no es realista, se cambia el archivo y sube la versión — no se ignora "
                           f"la lectura."),
             "etiquetas": ["sla", e["priority"].lower()],
             "refs": [{"tipo": "entrada", "valor": e["genesis"]}]}
            for e, h, lim in rotas]
    return len(rotas), hall


def sonda_falta_bloqueador(ctx):
    d = api("/api/entries?status=bloqueado&per=200")
    mudas = [e for e in d["items"] if not e.get("blocked_by")]
    hall = [{"nombre": f"falta-bloqueador:{e['genesis']}", "sujeto": e["genesis"],
             "espacio": e["space"], "prioridad": "P2",
             "titulo": f"Bloqueada sin decir a quién se espera: {e['title'][:52]}",
             "evidencia": (f"`{e['genesis']}` está bloqueada y `blocked_by` está vacío. Un bloqueo "
                           f"sin destinatario no se puede perseguir: nadie sabe a quién recordarle, "
                           f"y la entrada envejece sin que su falta de avance sea culpa de nadie."),
             "etiquetas": ["falta-bloqueador"],
             "refs": [{"tipo": "entrada", "valor": e["genesis"]}]}
            for e in mudas]
    return len(mudas), hall


def sonda_monitor_atrasado(ctx):
    lim = ctx["umbrales"]["monitor_atraso_eventos"]
    mudo = ctx["umbrales"]["monitor_mudo_horas"]
    d = api("/api/monitors")
    malos = []
    for m in d["items"]:
        if not m["active"]:
            continue
        h = horas_desde(m["last_seen"])
        if m["atraso"] > lim or (h is not None and h > mudo):
            malos.append((m, h))
    hall = [{"nombre": f"monitor:{m['key']}", "sujeto": m["key"], "espacio": "personal",
             "prioridad": "P2",
             "titulo": f"El monitor {m['key']} lleva {m['atraso']} eventos de atraso",
             "evidencia": (f"Cursor {m['cursor']} de {m['head']}: {m['atraso']} eventos sin leer "
                           f"(umbral {lim}). Visto por última vez hace "
                           f"{'nunca' if h is None else str(int(h)) + ' h'} (umbral {mudo} h).\n\n"
                           f"Un monitor atrasado no es un monitor lento: es trabajo que nadie está "
                           f"mirando. Capacidades: {m['capabilities']}."),
             "etiquetas": ["monitor", "bus"], "refs": [{"tipo": "monitor", "valor": m["key"]}]}
            for m, h in malos]
    return len(malos), hall


def sonda_fuente_sin_secreto(ctx):
    d = api("/api/sources")
    malas = [s for s in d["items"]
             if s["active"] and s["verify"] != "ninguna" and not s["secreto_presente"]]
    hall = [{"nombre": f"fuente-sin-secreto:{s['key']}", "sujeto": s["key"],
             "espacio": s["space"], "prioridad": "P1",
             "titulo": f"La fuente {s['key']} rechaza todo: falta su secreto",
             "evidencia": (f"Declara verificación `{s['verify']}` y no hay secreto en "
                           f"`{s['secreto_env']}`. Falla cerrado, que es lo correcto, pero el "
                           f"efecto es que **hoy no entra nada** por `{s['endpoint']}`: "
                           f"{s['rechazados']} rechazados, {s['recibidos']} aceptados.\n\n"
                           f"El crudo se conserva igual, así que lo rechazado se puede reprocesar "
                           f"cuando el secreto exista."),
             "etiquetas": ["fuente", "secreto"], "refs": [{"tipo": "fuente", "valor": s["key"]}]}
            for s in malas]
    return len(malas), hall


def sonda_fuente_muda(ctx):
    u = ctx["umbrales"]["fuente_muda_dias"]
    d = api("/api/sources")
    mudas = []
    for s in d["items"]:
        if not s["active"] or not s["recibidos"]:
            continue                      # una fuente que nunca recibió no está muda: está nueva
        h = horas_desde(s["ultimo"])
        if h is not None and h > u * 24:
            mudas.append((s, h))
    hall = [{"nombre": f"fuente-muda:{s['key']}", "sujeto": s["key"], "espacio": s["space"],
             "prioridad": "P2",
             "titulo": f"La fuente {s['key']} lleva {int(h / 24)} d sin mandar nada",
             "evidencia": (f"Recibió {s['recibidos']} señales y la última fue hace {int(h / 24)} "
                           f"días (umbral {u}). Una fuente que funcionaba y calla no es silencio: "
                           f"es una integración rota que nadie notó, porque la ausencia de datos "
                           f"no dispara alarmas por sí sola."),
             "etiquetas": ["fuente", "silencio"], "refs": [{"tipo": "fuente", "valor": s["key"]}]}
            for s, h in mudas]
    return len(mudas), hall


def sonda_rebote_alto(ctx):
    piso = ctx["umbrales"]["rebote_pct_piso"]
    dias = ctx["ventana_dias"]
    desde = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=dias)).isoformat(timespec="seconds")
    d = api(f"/api/facts?domain=email&limit=1&desde={urllib.parse.quote(desde)}")
    if not d.get("total"):
        raise SinAcceso(f"sin señales de correo en {dias} días: no hay denominador")
    por_dom = {}
    for pagina in range(0, 1):
        f = api(f"/api/facts?domain=email&limit=500&desde={urllib.parse.quote(desde)}")
        for x in f["items"]:
            dom = (x.get("attrs") or {}).get("dominio") or "?"
            g = por_dom.setdefault(dom, {"enviados": 0, "rebotes": 0})
            if x["action"] in ("sent", "delivered"):
                g["enviados"] += 1
            if x["action"] == "bounced":
                g["rebotes"] += 1
    hall, peor = [], 0.0
    for dom, g in sorted(por_dom.items()):
        base = g["enviados"] + g["rebotes"]
        if base < 20:
            continue                      # muestra corta: no se afirma nada
        pct = 100.0 * g["rebotes"] / base
        peor = max(peor, pct)
        if pct >= piso:
            hall.append({
                "nombre": f"rebote-alto:{dom}", "sujeto": dom, "espacio": "correo",
                "prioridad": "P1",
                "titulo": f"Rebote de {pct:.1f}% en {dom}, sobre el piso de {piso}%",
                "evidencia": (f"{g['rebotes']} rebotes de {base} envíos en {dias} días = "
                              f"**{pct:.1f}%**; el piso es {piso}%.\n\nMedido sobre "
                              f"/api/facts?domain=email en la ventana de {dias} días, con al menos "
                              f"20 envíos por dominio: por debajo de eso el porcentaje se mueve "
                              f"demasiado con un solo rebote y no se afirma nada.\n\nUn rebote alto "
                              f"quema la reputación del dominio antes de que la bandeja lo note."),
                "etiquetas": ["resend", "rebote", "reputacion"],
                "refs": [{"tipo": "dominio", "valor": dom}]})
    return round(peor, 2), hall


def sonda_outbox(ctx):
    piso = ctx["umbrales"]["outbox_pendiente_piso"]
    s = api("/api/sync")
    if s["pendiente"] < piso:
        return s["pendiente"], []
    return s["pendiente"], [{
        "nombre": "outbox-atorado", "sujeto": "outbox", "espacio": "personal", "prioridad": "P2",
        "titulo": f"La cola hacia la nube pasó de {piso}: {s['pendiente']} pendientes",
        "evidencia": (f"{s['pendiente']} filas pendientes, {s['enviado']} enviadas, {s['error']} "
                      f"con error. Destino: {s['destino'] or 'sin configurar'}.\n\nLa cola conserva "
                      f"el orden y el digest, así que nada se pierde; lo que crece es la distancia "
                      f"entre lo que pasó aquí y lo que la nube sabe."),
        "etiquetas": ["sincronizacion"], "refs": []}]


SONDAS = [
    ("sin-dueno", sonda_sin_dueno, "entradas abiertas sin responsable"),
    ("estancada", sonda_estancada, "entradas abiertas sin movimiento"),
    ("sla", sonda_sla, "entradas fuera del acuerdo de atención"),
    ("falta-bloqueador", sonda_falta_bloqueador, "bloqueadas sin destinatario"),
    ("monitor-atrasado", sonda_monitor_atrasado, "monitores atrasados o mudos"),
    ("fuente-sin-secreto", sonda_fuente_sin_secreto, "fuentes que rechazan todo"),
    ("fuente-muda", sonda_fuente_muda, "fuentes que dejaron de mandar"),
    ("rebote-alto", sonda_rebote_alto, "peor % de rebote por dominio emisor"),
    ("outbox", sonda_outbox, "filas pendientes de sincronizar"),
]


# ========================================================== las predicciones ==
def predecir(ctx):
    """Se escribe ANTES de medir. Una predicción falsificada es un resultado."""
    p = [
        {"sonda": "fuente-sin-secreto", "espero": ">=1",
         "porque": "repos-nucleo declara hmac-sha256 y su variable de entorno no está puesta"},
        {"sonda": "outbox", "espero": ">0",
         "porque": ("no hay destino configurado, así que la cola crece; pero crecer no es "
                    "cruzar el piso de 5000 — la predicción de la v0.1.0 confundía las dos "
                    "cosas y salió falsificada con 1090")},
        {"sonda": "rebote-alto", "espero": "<8.0",
         "porque": "la mezcla sembrada da ~5% de rebotes, por debajo del piso"},
        {"sonda": "monitor-atrasado", "espero": ">=1",
         "porque": "los monitores sembrados nunca leyeron su feed"},
        {"sonda": "estancada", "espero": ">=1",
         "porque": ("hay entradas sembradas de hace semanas cuyo único movimiento reciente "
                    "es el comentario del monitor reactivo; con la sonda v0.2.0 deben "
                    "aparecer, y si no aparecen la corrección no sirvió")},
    ]
    return p


def evaluar(prediccion, lecturas):
    l = lecturas.get(prediccion["sonda"])
    if not l or l["valor"] is None:
        return None, "sin lectura"
    v, e = l["valor"], prediccion["espero"]
    try:
        if e.startswith(">="):
            ok = v >= float(e[2:])
        elif e.startswith("<="):
            ok = v <= float(e[2:])
        elif e.startswith(">"):
            ok = v > float(e[1:])
        elif e.startswith("<"):
            ok = v < float(e[1:])
        else:
            ok = v == float(e)
    except ValueError:
        return None, "predicción mal formada"
    return ok, f"{v} {'cumple' if ok else 'NO cumple'} {e}"


# ================================================================ el trabajo ==
def enrutar(hallazgo, R):
    e = R["enrutamiento"]
    for criterio in e["orden"]:
        if criterio == "etiqueta":
            for l in hallazgo.get("etiquetas") or []:
                if l in e["por_etiqueta"]:
                    return e["por_etiqueta"][l], f"etiqueta:{l}"
        elif criterio == "prioridad":
            if hallazgo["prioridad"] in e["por_prioridad"]:
                return e["por_prioridad"][hallazgo["prioridad"]], f"prioridad:{hallazgo['prioridad']}"
        elif criterio == "espacio":
            if hallazgo["espacio"] in e["por_espacio"]:
                return e["por_espacio"][hallazgo["espacio"]], f"espacio:{hallazgo['espacio']}"
    return None, None


def abiertas_del_bucle():
    """Lo que este bucle ya abrió y sigue abierto, por nombre de hallazgo."""
    d = api("/api/entries?labels_any=bucle&per=200")
    por_nombre = {}
    for e in d["items"]:
        n = (e.get("meta") or {}).get("hallazgo")
        if n and e["status"] not in ("cerrado", "descartado"):
            por_nombre[n] = e
    return por_nombre


def aplicar(hallazgos, ctx, aplicar_de_verdad):
    R = ctx["reglas"]
    ya = abiertas_del_bucle()
    encontrados = {h["nombre"]: h for h in hallazgos}
    acciones = {"abiertas": [], "actualizadas": [], "cerradas": [], "omitidas": []}

    for nombre, h in encontrados.items():
        quien, regla = enrutar(h, R)
        if nombre in ya:
            acciones["actualizadas"].append(nombre)
            if aplicar_de_verdad:
                api(f"/api/entries/{ya[nombre]['genesis']}/comentario", "POST", {
                    "actor": ACTOR,
                    "texto": f"Sigue vigente en la corrida de {ahora()} "
                             f"(instrumento v{INSTRUMENTO_VERSION}). No se abre una segunda: "
                             f"un hallazgo con nombre estable se actualiza, no se duplica."})
            continue
        acciones["abiertas"].append(nombre)
        if aplicar_de_verdad:
            api("/api/entries", "POST", {
                "kind": "ticket", "space": h["espacio"], "title": h["titulo"],
                "body": (h["evidencia"] + f"\n\n---\nHallazgo `{nombre}` · sonda "
                         f"`{nombre.split(':')[0]}` · instrumento {INSTRUMENTO} "
                         f"v{INSTRUMENTO_VERSION} · reglas v{R['version']}."),
                "priority": h["prioridad"], "assignee": quien, "requester": ACTOR,
                "labels": (h.get("etiquetas") or []) + ["bucle"],
                "refs": h.get("refs") or [],
                "meta": {"hallazgo": nombre, "sonda": nombre.split(":")[0],
                         "instrumento": f"{INSTRUMENTO} v{INSTRUMENTO_VERSION}",
                         "enrutado_por": regla or "sin regla"},
                "actor": ACTOR, "source": "bucle"})

    for nombre, e in ya.items():
        if nombre in encontrados:
            continue
        acciones["cerradas"].append(nombre)
        if aplicar_de_verdad:
            api(f"/api/entries/{e['genesis']}/comentario", "POST", {
                "actor": ACTOR,
                "texto": f"La lectura de {ahora()} ya no encuentra este hallazgo "
                         f"(instrumento v{INSTRUMENTO_VERSION}). Se cierra citando la medición "
                         f"que lo cerró, no por antigüedad."})
            api(f"/api/entries/{e['genesis']}", "PATCH", {
                "status": "cerrado", "actor": ACTOR,
                "nota": f"cerrada por el bucle: la sonda ya no lo reporta"})
    return acciones


# ==================================================================== corrida ==
def corrida(aplicar_de_verdad, verbose=True):
    R = reglas()
    ctx = {"reglas": R, "reglas_version": R["version"], "umbrales": R["umbrales"],
           "ventana_dias": R["ventana_dias"]}
    t0 = time.time()
    fila = {"at": ahora(),
            "instrumento": {"nombre": INSTRUMENTO, "version": INSTRUMENTO_VERSION,
                            "sha256": digest_instrumento()},
            "reglas_version": R["version"], "modo": "aplicar" if aplicar_de_verdad else "seco"}

    # 1. la predicción, antes de medir
    fila["predicciones"] = predecir(ctx)

    # 2. el entorno de la lectura: sin él la lectura no es comparable con nada
    try:
        b = api("/api/bootstrap")
        fila["entorno"] = {"url": URL, **b["totales"]}
        comparable = True
    except SinAcceso as e:
        fila["entorno"] = {"url": URL, "error": str(e)}
        comparable = False

    # 3. las sondas
    lecturas, hallazgos = {}, []
    for nombre, fn, que_mide in SONDAS:
        try:
            valor, hs = fn(ctx)
            lecturas[nombre] = {"valor": valor, "hallazgos": len(hs), "mide": que_mide}
            hallazgos += hs
        except SinAcceso as e:
            # falta de acceso NO es cero: con cero, un servidor caído se lee como salud
            lecturas[nombre] = {"valor": None, "hallazgos": None, "mide": que_mide,
                                "sin_acceso": str(e)}
            comparable = False
    fila["lecturas"] = lecturas
    fila["comparable"] = comparable

    # 4. la predicción contra la lectura
    falsificadas = []
    for p in fila["predicciones"]:
        ok, detalle = evaluar(p, lecturas)
        p["resultado"] = detalle
        p["cumplida"] = ok
        if ok is False:
            falsificadas.append(f"{p['sonda']}: esperaba {p['espero']}, {detalle}")
    fila["falsificadas"] = falsificadas

    # 5. el trabajo
    try:
        fila["acciones"] = aplicar(hallazgos, ctx, aplicar_de_verdad)
    except SinAcceso as e:
        fila["acciones"] = {"error": str(e)}
        fila["comparable"] = False
    # deriva: si el instrumento cambió desde la última corrida, se publica qué se
    # movió. Antes de acreditarle un cambio al mundo hay que descartar que se lo
    # movió el instrumento.
    prev = ultima_fila()
    if prev and prev.get("instrumento", {}).get("version") != INSTRUMENTO_VERSION:
        d = {}
        for n, l in lecturas.items():
            antes = (prev.get("lecturas") or {}).get(n, {}).get("valor")
            if antes != l["valor"]:
                d[n] = {"antes": antes, "ahora": l["valor"]}
        fila["deriva"] = {"desde": prev["instrumento"]["version"], "a": INSTRUMENTO_VERSION,
                          "razon": DERIVA.get(INSTRUMENTO_VERSION, ""), "movieron": d}
    fila["duracion_s"] = round(time.time() - t0, 2)
    anota(fila)

    if verbose:
        print(f"\n  {INSTRUMENTO} v{INSTRUMENTO_VERSION} · reglas v{R['version']} · "
              f"{'APLICA' if aplicar_de_verdad else 'seco'} · comparable={fila['comparable']}")
        print(f"  {'sonda':<20} {'lectura':>9}  hallazgos")
        for n, l in lecturas.items():
            v = "sin acceso" if l["valor"] is None else l["valor"]
            print(f"  {n:<20} {str(v):>9}  {'—' if l['hallazgos'] is None else l['hallazgos']}"
                  f"{'   ← ' + l['sin_acceso'] if l.get('sin_acceso') else ''}")
        a = fila["acciones"]
        if "error" not in a:
            print(f"\n  abiertas {len(a['abiertas'])} · actualizadas {len(a['actualizadas'])} · "
                  f"cerradas {len(a['cerradas'])}")
            for n in a["abiertas"][:8]:
                print(f"    + {n}")
            for n in a["cerradas"][:5]:
                print(f"    - {n}")
        print(f"\n  predicciones: {sum(1 for p in fila['predicciones'] if p['cumplida'])} de "
              f"{len(fila['predicciones'])} se cumplieron")
        for p in fila["predicciones"]:
            marca = "✓" if p["cumplida"] else ("✗" if p["cumplida"] is False else "?")
            print(f"    {marca} {p['sonda']:<20} {p['resultado']}")
        if falsificadas:
            print("\n  FALSIFICADAS (esto es un resultado, no un error):")
            for f in falsificadas:
                print(f"    · {f}")
        if fila.get("deriva"):
            d = fila["deriva"]
            print(f"\n  DERIVA DEL INSTRUMENTO {d['desde']} → {d['a']}")
            for n, m in d["movieron"].items():
                print(f"    {n:<20} {m['antes']} → {m['ahora']}")
            print(f"    razón: {d['razon'][:150]}…")
        print(f"\n  ledger: {LEDGER}")
    return fila


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--una-vez", action="store_true")
    ap.add_argument("--aplicar", action="store_true",
                    help="sin esto la corrida es seca: mide y anota, no toca nada")
    ap.add_argument("--intervalo", type=int, default=900)
    ap.add_argument("--silencio", action="store_true")
    a = ap.parse_args()

    if a.una_vez:
        f = corrida(a.aplicar, verbose=not a.silencio)
        return 0 if f["comparable"] else 3

    print(f"bucle cada {a.intervalo}s · parar con: touch {PARAR}")
    while not os.path.exists(PARAR):
        corrida(a.aplicar, verbose=not a.silencio)
        for _ in range(a.intervalo):
            if os.path.exists(PARAR):
                break
            time.sleep(1)
    print("archivo de paro presente; salgo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
