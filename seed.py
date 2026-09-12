#!/usr/bin/env python3
"""
Siembra Event Log con un estado de demostración plausible y determinista.

Nada aquí es aleatorio: todo sale de un SHA1 de la identidad del objeto, así que
la misma siembra produce el mismo tablero en cualquier máquina. Los espacios,
las personas y los dominios de correo son los reales del ecosistema; el
contenido de los tickets y el tráfico de correo son de demostración.
"""
import hashlib, json, os, random, sqlite3, sys, datetime as dt
import core

TODAY = dt.datetime(2026, 9, 10, 17, 0, tzinfo=dt.timezone.utc)


def h(*p):
    return int(hashlib.sha1("|".join(map(str, p)).encode()).hexdigest(), 16)


def pick(seed, xs):
    return xs[seed % len(xs)]


def ts(days_ago, hour=None, seed=0):
    d = TODAY - dt.timedelta(days=days_ago)
    d = d.replace(hour=hour if hour is not None else (seed % 13) + 7,
                  minute=(seed >> 4) % 60, second=(seed >> 9) % 60)
    return d.isoformat(timespec="seconds")


SPACES = [
    ("northwind", "Northwind · cliente",  "cliente",   "#38bdf8",
     {"campos": [{"clave": "ticket_externo", "etiqueta": "Ticket del cliente", "tipo": "texto"},
                 {"clave": "banda", "etiqueta": "Banda", "tipo": "opcion",
                  "opciones": ["QA", "UAT", "PROD"]},
                 {"clave": "marca", "etiqueta": "Marca", "tipo": "opcion",
                  "opciones": ["NORTE", "SUR"]}]}),
    ("mirador",   "Mirador · cliente",    "cliente",   "#56d9a3",
     {"campos": [{"clave": "cuenta", "etiqueta": "Cuenta", "tipo": "texto"},
                 {"clave": "canal", "etiqueta": "Canal", "tipo": "opcion",
                  "opciones": ["correo", "directorio", "mapa"]}]}),
    ("abastos",   "Abastos · cliente",    "cliente",   "#f3b74e",
     {"campos": [{"clave": "cotizacion", "etiqueta": "Cotización", "tipo": "texto"},
                 {"clave": "grado", "etiqueta": "Grado", "tipo": "texto"}]}),
    ("sonda",     "Sonda · producto",     "producto",  "#a78bfa",
     {"campos": [{"clave": "item", "etiqueta": "Item del registro", "tipo": "numero"},
                 {"clave": "paquete", "etiqueta": "Paquete", "tipo": "texto"}]}),
    ("nucleo",    "Núcleo · plataforma",  "producto",  "#2ec5ff",
     {"campos": [{"clave": "superficie", "etiqueta": "Superficie", "tipo": "opcion",
                  "opciones": ["runtime", "cli", "api", "escritorio"]}]}),
    ("correo",    "Correo · operación",   "operacion", "#fb7185",
     {"campos": [{"clave": "dominio", "etiqueta": "Dominio", "tipo": "texto"},
                 {"clave": "grado", "etiqueta": "Grado de entrega", "tipo": "texto"}]}),
    ("personal",  "Personal",             "personal",  "#94a1b8", {"campos": []}),
]

ACTORS = [
    ("dana",            "Dana Ortiz",        "persona", "@dana",     "#2ec5ff", "DO"),
    ("agente-auditor",  "Agente · auditor",  "agente",  "@auditor",  "#a78bfa", "AA"),
    ("agente-ejecutor", "Agente · ejecutor", "agente",  "@ejecutor", "#56d9a3", "AE"),
    ("arnes-sonda",     "Arnés de Sonda",    "agente",  "@arnes",    "#f3b74e", "AS"),
    ("monitor-correo",  "Monitor de correo", "agente",  "@correo",   "#fb7185", "MC"),
    ("lider-tablero",   "Líder del tablero", "externo", "@lider",    "#7cc6f5", "LT"),
    ("proveedor-dns",   "Proveedor de DNS",  "externo", "@dns",      "#f472b6", "PD"),
    ("contacto-externo","Contacto externo",  "externo", "@externo",  "#fbbf24", "CX"),
    ("sistema",         "Event Log",         "sistema", "@log",      "#5d6b82", "EL"),
]

TICKETS = [
    ("northwind", "Confirmar con el proveedor de telefonía los identificadores de cola",
     "Hoy resolvemos la cola por nombre. Necesitamos el identificador estable de cada una y el "
     "contrato de reintento cuando está saturada.\n\nSin eso, cualquier cambio de nombre del "
     "lado de ellos nos rompe la transferencia en silencio.", "bloqueado", "P1",
     "lider-tablero", "dana", ["telefonia", "transferencia"],
     [{"tipo": "externo", "valor": "NW-718"}],
     {"ticket_externo": "NW-718", "banda": "UAT", "marca": "NORTE"},
     "el proveedor de telefonía, vía su gerente de cuenta", 6),
    ("northwind", "El linaje de etiquetas se detuvo en v0.6.5 mientras los releases llegaron a v0.7.9",
     "El manifiesto resuelve la etiqueta caminando a la alcanzable más cercana, así que doce "
     "releases declaran una versión que no es la suya.\n\nLos digests siguen correctos: la capa "
     "de contenido aguanta, la etiqueta miente.", "en curso", "P1", "dana", "lider-tablero",
     ["versionado", "release"], [{"tipo": "externo", "valor": "NW-561"}],
     {"ticket_externo": "NW-561", "banda": "PROD"}, None, 12),
    ("northwind", "Un repo sin políticas de rama acepta merges a la principal sin build",
     "El bloque de PR del YAML no crea validación; la política de rama sí. El repo se ve bien "
     "configurado y no lo está.", "entrante", "P2", None, "dana", ["cicd", "riesgo"],
     [{"tipo": "externo", "valor": "NW-576"}], {"ticket_externo": "NW-576"}, None, 9),
    ("mirador", "El webhook de respuestas entrantes sigue en fase 2",
     "Las respuestas al dominio de salida no tienen destino: enviamos y nadie ve lo que "
     "contestan. Falta el receptor y la regla de reenvío al buzón humano.", "aceptado", "P1",
     "agente-ejecutor", "dana", ["correo", "webhook", "salida"],
     [{"tipo": "url", "valor": "https://correo.mirador.mx"}],
     {"cuenta": "Mirador", "canal": "correo"}, None, 4),
    ("mirador", "El enriquecimiento firmográfico es el próximo cuello del motor",
     "El puntaje ya distingue cuentas activas de dormidas, pero sin tamaño ni giro las listas se "
     "ordenan por señales débiles.", "entrante", "P2", None, "dana",
     ["enriquecimiento", "datos"], [], {"cuenta": "Mirador", "canal": "mapa"}, None, 15),
    ("abastos", "El emparejador no adivina: grado y envase quedan obligatorios",
     "Un pedido sin grado hacía que el emparejador eligiera el más barato, y eso ya costó una "
     "cotización mal armada.\n\nLa alerta del intake vivía dentro de lo que protegía.",
     "cerrado", "P1", "dana", "dana", ["catalogo", "emparejador"], [],
     {"cotizacion": "AB-2026-0412", "grado": "obligatorio"}, None, 21),
    ("sonda", "Un verde filtrado nunca es un verde de suite",
     "Cinco demostraciones esta semana: la suite completa es el único instrumento para la ruptura "
     "fuera del filtro. Un filtro que no casa con nada reporta verde.", "en revision", "P0",
     "agente-auditor", "agente-ejecutor", ["evidencia", "gate"],
     [{"tipo": "commit", "valor": "3b105dc"}], {"item": 113, "paquete": "202609110000"}, None, 1),
    ("sonda", "Las filas registran lo que adquirieron, no lo que se infiere del tamaño",
     "El rendimiento se lee de la fila, no se deriva del peso del paquete.", "cerrado", "P2",
     "agente-ejecutor", "agente-auditor", ["arnes", "medicion"], [], {"item": 109}, None, 3),
    ("nucleo", "El manifiesto necesita una ruta, no un nombre suelto",
     "Un binario con nombre pelado sólo resuelve por PATH, y el demonio cachea el manifiesto: hay "
     "que recargar por el supervisor, no existe operación de recarga.", "en curso", "P2",
     "agente-ejecutor", "dana", ["demonio", "manifiesto"], [], {"superficie": "runtime"}, None, 7),
    ("correo", "Bajar el TTL de los MX antes de mover el dominio",
     "El proveedor tiene el control del DNS. Con TTL de cuatro horas, cualquier corrección tarda "
     "media jornada en propagarse.", "bloqueado", "P1", "proveedor-dns", "dana",
     ["dns", "proveedor-dns"], [], {"dominio": "correo.mirador.mx"}, "el proveedor de DNS", 2),
    ("correo", "Estamos en el tope de dominios del proveedor de envío",
     "El alta responde 403. Liberar un slot no alcanza: se pierde el dominio al probarlo. Los "
     "demás siguen vivos.", "en revision", "P1", "dana", "dana", ["correo", "limite"], [],
     {"dominio": "*"}, None, 5),
    ("personal", "Decidir si el tablero de demostración se queda o se archiva",
     "Cumplió su propósito de mostrar el punto. Decidir si se vuelve producto.", "entrante",
     "P3", "dana", "dana", ["decision"], [], {}, None, 0),
]

NOTAS = [
    ("northwind", "Orden de autoridad cuando dos instrucciones se contradicen",
     "Fijado en la auditoría de julio y aquí sólo se restablece:\n\n"
     "1. el contrato del workspace padre\n2. las instrucciones del repo hijo más cercano\n"
     "3. los documentos canónicos\n4. el código, ADRs, pruebas y logs del repo dueño\n"
     "5. el gestor de tickets\n6. las notas\n\n"
     "Consecuencia práctica: cuando una guía general diga una cosa y el documento de versionado "
     "del hijo diga otra, manda el hijo. Así se resolvió la guía retractada de julio, que "
     "sobrevivió tres semanas en el resumen después de corregirse en el original.",
     ["autoridad", "proceso"], {"ticket_externo": "NW-575"}, 24),
    ("correo", "Los patrones de dirección en el mercado local",
     "48% `inicialapellido` más 42% `nombre.apellido` cubren el 90% del mercado con dos "
     "candidatos.\n\nEl rastreo mide lo publicado, no lo existente: la ausencia de un patrón en "
     "la web no dice que la dirección no exista.", ["patrones", "medicion"], {}, 30),
    ("correo", "Enrolar un dominio de cliente: el contrato de cuatro archivos",
     "El cuarto archivo es silenciosamente inerte si se omite. La trampa del DNS detrás de un "
     "proxy y la prueba de aceptación entrante son los dos pasos que nadie recuerda hasta que "
     "fallan.", ["calentamiento", "runbook"], {"dominio": "*"}, 18),
    ("sonda", "Toda afirmación lleva su tier",
     "`[RE]` dirección · `[SRC]` archivo:línea · `[DERIVED]` razonado, debe una regresión · "
     "`[MEASURED]` cita la lectura · `[UNVERIFIED]` una pista · `[FALSIFIED]` comprobado y falso, "
     "anotado para que nadie lo vuelva a derivar.\n\nUna protección vale lo que su sitio de "
     "llamada más angosto.", ["procedencia", "canon"], {}, 14),
    ("nucleo", "Las gráficas SVG realimentan la altura de su contenedor",
     "Si el SVG participa del flujo dentro de un contenedor flexible, su alto realimenta al "
     "contenedor, el observador de tamaño vuelve a medir más grande y la gráfica crece sin fin. "
     "Llegó a 1975px antes de que se notara.\n\nPosicionado absoluto y guarda de medición.",
     ["frontend", "cicatriz"], {"superficie": "api"}, 0),
    ("personal", "Simular antes de intentar, en tareas con costo por intento",
     "Arnés de simulación sin costo, iterar en frío, y un solo intento real.", ["metodo"], {}, 40),
]

FUENTES = [
    ("correo-flota", "Correo · flota de calentamiento", "resend", "correo",
     {"proposito": "Rastro normalizado de todo el correo que sale y entra de la flota. Alimenta "
                   "el almacén; no abre tickets salvo rebote duro o queja.",
      "dominios": ["correo.mirador.mx", "avisos.abastos.mx", "hola.northwind.io", "notas.nucleo.dev"],
      "responsable": "dana", "retencion": "crudo indefinido"},
     "svix", "resend", 0),
    ("arnes-sonda", "Arnés de Sonda", "generico", "sonda",
     {"proposito": "Filas de medición del bucle de calidad. Una fila fallida abre entrada.",
      "responsable": "agente-auditor", "contrato": "ledger.jsonl append-only"},
     "hmac-sha256", "generico", 1),
    ("repos-nucleo", "Repositorios de Núcleo", "generico", "nucleo",
     {"proposito": "Push, PR y release de los repos de producto.",
      "responsable": "dana"}, "hmac-sha256", "generico", 0),
    ("intake-publico", "Intake público", "intake", "general",
     {"proposito": "Cualquiera con el enlace puede dejar una tarea. Siempre abre entrada, siempre "
                   "en estado entrante, siempre con el solicitante declarado.",
      "responsable": "dana", "moderacion": "revisión manual antes de aceptar"},
     "ninguna", "intake", 1),
]

MONITORES = [
    ("shell-dana", "Terminal de Dana", "dana", "shell",
     {"abiertos": True, "assignee": ["dana"], "event": ["entrada.*"]},
     ["observar"], 900),
    ("agente-correo", "Monitor de correo", "dana", "agente",
     {"space": ["correo"], "domain": ["email"], "action": ["bounced", "complained"]},
     ["observar", "reaccionar", "actuar"], 600),
    ("arnes-sonda", "Arnés del bucle de calidad", "agente-auditor", "arnes",
     {"space": ["sonda"], "labels_any": ["gate", "evidencia"],
      "event": ["entrada.creada", "entrada.movida"]},
     ["observar", "actuar"], 1800),
]

# --- tráfico de correo de la flota de warming (dominio real, contenido de demo) ---
DOMINIOS = ["correo.mirador.mx", "avisos.abastos.mx", "hola.northwind.io", "notas.nucleo.dev"]
BUZONES = ["hola", "contacto", "ventas", "no-reply", "equipo"]
ACCIONES = [("sent", 34), ("delivered", 32), ("opened", 18), ("clicked", 7),
            ("bounced", 5), ("complained", 1), ("received", 3)]
DESTINOS = ["gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "empresa.mx",
            "corporativo.com.mx", "proton.me"]


def w_pick(seed, pares):
    tot = sum(w for _, w in pares)
    x = seed % tot
    a = 0
    for v, w in pares:
        a += w
        if x < a:
            return v
    return pares[-1][0]


def main():
    if os.path.exists(core.DB):
        os.remove(core.DB)
    for ext in ("-wal", "-shm"):
        p = core.DB + ext
        if os.path.exists(p):
            os.remove(p)
    c = core.connect()
    core.init(c)

    for k, n, kind, color, sch in SPACES:
        c.execute("INSERT INTO spaces (key,name,kind,color,schema_json,meta,created) "
                  "VALUES (?,?,?,?,?,?,?)",
                  (k, n, kind, color, core.jdump(sch), "{}", ts(60)))
    for k, n, kind, handle, color, ini in ACTORS:
        c.execute("INSERT INTO actors (key,name,kind,handle,email,color,initials,meta,created) "
                  "VALUES (?,?,?,?,?,?,?,?,?)",
                  (k, n, kind, handle, "", color, ini, "{}", ts(60)))
    for k, n, kind, space, ctx, verify, norm, crea in FUENTES:
        c.execute("""INSERT INTO sources (key,name,kind,space,context,verify,secret_hash,
                     normalizer,crea_entrada,created) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                  (k, n, kind, space, core.jdump(ctx), verify,
                   core.token_hash("demo-" + k), norm, crea, ts(45)))
        core.emit(c, "fuente.registrada", space=space, actor="dana", at=ts(45),
                  payload={"fuente": k, "nombre": n, "contexto": ctx, "verify": verify})

    # ---- entradas
    for i, (sp, title, body, status, prio, asg, req, labels, refs, meta, blocked, ago) in \
            enumerate(TICKETS):
        s = h("ticket", title)
        g = f"ev-{(TODAY - dt.timedelta(days=ago)):%Y%m%d}-{s % 0xFFFFFF:06x}"
        core.put_entry(c, {"genesis": g, "kind": "ticket", "space": sp, "title": title,
                           "body": body, "status": "entrante", "priority": prio,
                           "assignee": None, "requester": req, "labels": labels,
                           "refs": refs, "meta": meta, "created": ts(ago, seed=s)},
                       actor=req, source="ui")
        # el camino real: aceptar, asignar, avanzar. Cada paso ocurre en su
        # momento, repartido entre el alta y hoy, no todo en el mismo instante.
        pasos = []
        if status != "entrante":
            pasos.append(({"status": "aceptado", "assignee": asg}, "dana", "aceptado del intake"))
        if status in ("en curso", "en revision", "bloqueado", "cerrado", "descartado"):
            pasos.append(({"status": "en curso"}, asg or "dana", None))
        if status == "bloqueado":
            pasos.append(({"status": "bloqueado", "blocked_by": blocked}, asg or "dana",
                          f"se espera a {blocked}"))
        if status in ("en revision", "cerrado"):
            pasos.append(({"status": "en revision"}, asg or "dana", None))
        if status == "cerrado":
            pasos.append(({"status": "cerrado"}, "dana", "verificado y cerrado"))
        for k, (cambio, quien, nota) in enumerate(pasos, start=1):
            cuando = ts(max(0, int(ago * (1 - k / (len(pasos) + 1)))), seed=s + k)
            core.update_entry(c, g, cambio, actor=quien, note=nota, at=cuando)

    for sp, title, body, labels, meta, ago in NOTAS:
        s = h("nota", title)
        g = f"ev-{(TODAY - dt.timedelta(days=ago)):%Y%m%d}-{s % 0xFFFFFF:06x}"
        core.put_entry(c, {"genesis": g, "kind": "nota", "space": sp, "title": title,
                           "body": body, "status": "aceptado", "priority": "P3",
                           "requester": "dana", "labels": labels, "meta": meta,
                           "created": ts(ago, seed=s)}, actor="dana", source="ui")

    # ---- señales: 21 días de tráfico de la flota
    n_fact = 0
    for d in range(21, -1, -1):
        base = h("dia", d)
        vol = 26 + (base % 30)
        for j in range(vol):
            s = h("correo", d, j)
            dom = pick(s, DOMINIOS)
            action = w_pick(s >> 5, ACCIONES)
            buzon = pick(s >> 11, BUZONES)
            dest = pick(s >> 17, DESTINOS)
            mid = f"{s % 0xFFFFFFFF:08x}-{(s >> 32) % 0xFFFF:04x}"
            occurred = ts(d, hour=(s % 14) + 6, seed=s)
            body = {"type": f"email.{action}",
                    "created_at": occurred,
                    "data": {"email_id": mid,
                             "from": f"{buzon}@{dom}",
                             "to": [f"{'contacto' if s % 3 else 'hola'}@{dest}"],
                             "subject": pick(s >> 23, [
                                 "Seguimiento de la propuesta",
                                 "Confirmación de recepción",
                                 "Materiales del taller",
                                 "Disponibilidad para la próxima semana",
                                 "Resumen de la sesión"])}}
            raw = core.jdump(body)
            rd = hashlib.sha256(raw.encode()).hexdigest()
            cur = c.execute("""INSERT INTO raw_payloads (source,received_at,headers,body,
                               digest,verified,remote) VALUES (?,?,?,?,?,?,?)""",
                            ("correo-flota", occurred,
                             core.jdump({"svix-id": f"msg_{mid}", "content-type": "application/json"}),
                             raw, rd, 1, "44.228.126.217"))
            raw_id = cur.lastrowid
            attrs = {"dominio": dom, "buzon": buzon, "destino": dest,
                     "asunto": body["data"]["subject"], "proveedor": "correo",
                     "flota": "warming"}
            fd = core.digest_of({"source": "correo-flota", "domain": "email",
                                 "action": action, "subject_id": mid, "at": occurred})
            seq = core.emit(c, "senal.ingerida", space="correo", actor="monitor-correo",
                            at=occurred,
                            payload={"fuente": "correo-flota", "domain": "email",
                                     "action": action, "subject_id": mid,
                                     "dominio": dom, "destino": dest})
            c.execute("""INSERT INTO facts (raw_id,source,space,occurred_at,ingested_at,
                         domain,action,subject_type,subject_id,actor,target,attrs,digest,seq)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (raw_id, "correo-flota", "correo", occurred, occurred, "email",
                       action, "email", mid, f"{buzon}@{dom}", f"@{dest}",
                       core.jdump(attrs), fd, seq))
            n_fact += 1
    c.execute("UPDATE sources SET recibidos=?, ultimo=? WHERE key='correo-flota'",
              (n_fact, ts(0)))

    # una queja abre entrada: es la regla de la fuente, no una excepción
    q = c.execute("""SELECT * FROM facts WHERE action='complained'
                     ORDER BY occurred_at DESC LIMIT 1""").fetchone()
    if q:
        a = core.jload(q["attrs"])
        core.put_entry(c, {
            "kind": "ticket", "space": "correo", "priority": "P0",
            "title": f"Queja de spam en {a['dominio']} — revisar la flota antes de seguir enviando",
            "body": f"La señal `email.complained` llegó de `{q['subject_id']}` hacia "
                    f"`{a['destino']}`. Una queja pesa mucho más que un rebote en la "
                    f"reputación del dominio.\n\nRegla de la fuente: una queja abre entrada "
                    f"en P0 y detiene el incremento de volumen hasta revisar.",
            "labels": ["resend", "reputacion", "automatico"],
            "refs": [{"tipo": "senal", "valor": q["subject_id"]}],
            "meta": {"dominio": a["dominio"], "grado": "en revisión"},
            "requester": "monitor-correo"}, actor="monitor-correo", source="webhook:correo-flota")

    # ---- monitores inscritos
    for k, n, owner, kind, filt, caps, lease in MONITORES:
        c.execute("""INSERT INTO monitors (key,name,owner,kind,filter,capabilities,cursor,
                     lease_seconds,token_hash,created,last_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                  (k, n, owner, kind, core.jdump(filt), core.jdump(caps), 0, lease,
                   core.token_hash("demo-" + k), ts(30), ts(0)))
        core.emit(c, "monitor.registrado", actor=owner, at=ts(30),
                  payload={"monitor": k, "nombre": n, "filtro": filt, "capacidades": caps})

    c.commit()
    n = lambda q: c.execute(q).fetchone()[0]
    print(f"espacios {n('SELECT COUNT(*) FROM spaces')} · "
          f"actores {n('SELECT COUNT(*) FROM actors')} · "
          f"entradas {n('SELECT COUNT(*) FROM entries')} "
          f"(tickets {n(chr(39).join(['SELECT COUNT(*) FROM entries WHERE kind=', 'ticket', '']))}, "
          f"notas {n(chr(39).join(['SELECT COUNT(*) FROM entries WHERE kind=', 'nota', '']))})")
    print(f"señales {n('SELECT COUNT(*) FROM facts')} · "
          f"crudo {n('SELECT COUNT(*) FROM raw_payloads')} · "
          f"eventos {n('SELECT COUNT(*) FROM events')} · "
          f"revisiones {n('SELECT COUNT(*) FROM revisions')}")
    print(f"fuentes {n('SELECT COUNT(*) FROM sources')} · "
          f"monitores {n('SELECT COUNT(*) FROM monitors')} · "
          f"outbox pendiente {n(chr(39).join(['SELECT COUNT(*) FROM outbox WHERE state=', 'pendiente', '']))}")
    print(f"db: {core.DB}")
    c.close()


if __name__ == "__main__":
    main()
