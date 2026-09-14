#!/usr/bin/env python3
"""
Siembra Event Log con un tablero de demostración plausible y determinista.

Nada aquí es aleatorio: todo sale de un SHA1 de la identidad del objeto, así que
la misma siembra produce el mismo tablero en cualquier máquina.

Nada aquí es real tampoco. Las empresas, las personas, los dominios y los
incidentes son inventados. Existen para enseñar la forma del sistema: una
entrada que nace en el intake y camina hasta cerrada, una señal que abre trabajo
sin que nadie la pida, un monitor que se inscribe con capacidades y arrendamiento.

Para usarlo con lo tuyo, este archivo es el único que hay que tocar.
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
    ("northwind", "El proveedor de pagos no manda el folio de conciliación en el webhook",
     "Conciliamos por monto y fecha, que empata mal en cuanto hay dos cobros iguales el mismo "
     "día.\n\nPedimos el folio dentro del cuerpo del webhook. Sin él la conciliación es "
     "manual, y lo manual no escala a temporada alta.", "bloqueado", "P1",
     "lider-tablero", "dana", ["pagos", "conciliacion"],
     [{"tipo": "externo", "valor": "NW-718"}],
     {"ticket_externo": "NW-718", "banda": "UAT", "marca": "NORTE"},
     "el proveedor de pagos, vía su gerente de cuenta", 6),
    ("northwind", "El alta de pedidos acepta un reintento como pedido nuevo",
     "La tienda reintenta cuando su red falla, y cada reintento crea un pedido. Tres duplicados "
     "llegaron a PROD esta semana.\n\nFalta clave de idempotencia: la misma clave debe "
     "devolver el mismo folio, no fabricar otro.", "en curso", "P1", "dana", "lider-tablero",
     ["pedidos", "idempotencia"], [{"tipo": "externo", "valor": "NW-561"}],
     {"ticket_externo": "NW-561", "banda": "PROD"}, None, 12),
    ("northwind", "El inventario de SUR se sincroniza cada hora y la tienda promete minutos",
     "No es un defecto del sincronizador: es una promesa que la interfaz hace y el sistema "
     "nunca hizo. O baja la ventana, o cambia el texto.", "entrante", "P2", None, "dana",
     ["inventario", "expectativa"], [{"tipo": "externo", "valor": "NW-576"}],
     {"ticket_externo": "NW-576"}, None, 9),
    ("mirador", "Las fichas sin foto se ordenan igual que las completas",
     "El orden pesa cercanía y calificación, y trata una ficha a medio llenar como una "
     "completa. Premia a quien no llenó nada.\n\nVa un factor de completitud en el orden, no "
     "un filtro: esconderlas deja fuera a los que acaban de entrar.", "aceptado", "P1",
     "agente-ejecutor", "dana", ["busqueda", "orden"],
     [{"tipo": "url", "valor": "https://mirador.mx/directorio"}],
     {"cuenta": "Mirador", "canal": "directorio"}, None, 4),
    ("mirador", "La búsqueda por colonia no normaliza acentos",
     "«García» y «Garcia» devuelven listas distintas porque el índice guarda lo que se "
     "escribió, no su forma plegada.", "entrante", "P2", None, "dana",
     ["busqueda", "datos"], [], {"cuenta": "Mirador", "canal": "mapa"}, None, 15),
    ("abastos", "El catálogo aceptó dos unidades de medida para el mismo artículo",
     "Un artículo entró en cajas y en piezas, y el comparador sumó las dos como si fueran la "
     "misma cosa. La cotización salió con el triple del volumen.\n\nLa unidad ahora es parte "
     "de la clave del artículo, no un atributo suyo.", "cerrado", "P1", "dana", "dana",
     ["catalogo", "unidades"], [], {"cotizacion": "AB-2026-0118", "grado": "estándar"}, None, 21),
    ("sonda", "La exportación pierde filas cuando el lote pasa de una página",
     "El exportador pide la primera página y escribe; nunca pide la segunda. Un lote de 120 "
     "salió con 100 filas y sin un solo error.\n\nPasó en pruebas porque ninguna fixture "
     "llegaba a cien.", "en revision", "P0",
     "agente-auditor", "agente-ejecutor", ["exportacion", "paginacion"],
     [{"tipo": "commit", "valor": "a41c7e9"}], {"item": 42, "paquete": "202609080900"}, None, 1),
    ("sonda", "El contador de reintentos se reiniciaba en cada arranque",
     "El contador vivía en memoria, así que un reinicio devolvía los intentos a cero y el mismo "
     "trabajo se reintentaba para siempre.\n\nAhora el intento se anota en la fila, que es lo "
     "único que sobrevive al proceso.", "cerrado", "P2",
     "agente-ejecutor", "agente-auditor", ["reintentos", "estado"], [], {"item": 39}, None, 3),
    ("nucleo", "El despliegue no espera a que termine la migración",
     "El proceso nuevo arranca mientras la migración corre, lee una tabla a medio cambiar y se "
     "cae en el primer request.\n\nLa migración es un paso con su propia salida, no un hilo "
     "al lado del arranque.", "en curso", "P2",
     "agente-ejecutor", "dana", ["despliegue", "migracion"], [], {"superficie": "runtime"}, None, 7),
    ("correo", "El proveedor de DNS no delega el subdominio de envío",
     "Pedimos delegar `envio.` para mover sus registros sin tocar la zona principal. Sin la "
     "delegación cada cambio pasa por ellos y tarda una jornada.", "bloqueado", "P1",
     "proveedor-dns", "dana", ["dns", "delegacion"], [],
     {"dominio": "correo.mirador.mx"}, "el proveedor de DNS", 2),
    ("correo", "Las autorrespuestas cuentan como respuesta y ensucian la tasa",
     "«Estoy de vacaciones» entra por el mismo webhook que una respuesta real. La tasa subió "
     "nueve puntos en una semana sin que nadie contestara nada.\n\nHay encabezados que lo "
     "declaran; el normalizador todavía no los lee.", "en revision", "P1", "dana", "dana",
     ["correo", "metricas"], [], {"dominio": "*"}, None, 5),
    ("personal", "Elegir el formato del reporte de los lunes",
     "Tres párrafos y una lista de decisiones, o una tabla. Decidirlo una vez y dejar de "
     "improvisarlo cada semana.", "entrante", "P3", "dana", "dana", ["decision"], [], {}, None, 0),
]

NOTAS = [
    ("northwind", "Los tres ambientes no comparten datos, y es a propósito",
     "QA se borra cada noche, UAT lleva el corte que el cliente aprobó, PROD es de ellos.\n\n"
     "Copiar de PROD hacia abajo parece un atajo y arrastra direcciones reales al ambiente "
     "donde todo el equipo tiene acceso. Cuando UAT necesita un caso, se construye el caso.",
     ["ambientes", "convencion"], {"ticket_externo": "NW-575"}, 24),
    ("correo", "Un rebote duro y uno suave no se atienden igual",
     "El duro dice que la dirección no existe: se retira de la lista y no se reintenta. El "
     "suave dice que hoy no se pudo — buzón lleno, servidor ocupado — y se reintenta con "
     "espera.\n\nTratar un suave como duro tira contactos buenos. Tratar un duro como suave "
     "quema la reputación del dominio, que tarda semanas en volver.",
     ["correo", "runbook"], {}, 30),
    ("correo", "Los tres registros que el proveedor pide antes de dejarte enviar",
     "SPF autoriza quién envía, DKIM firma el mensaje, DMARC dice qué hacer cuando alguno "
     "falla. Los tres viven en el DNS del dominio, no en el proveedor.\n\nDMARC en `p=none` "
     "no protege: informa. Es el que se olvida, porque olvidarlo no rompe nada el primer día.",
     ["dns", "correo"], {"dominio": "*"}, 18),
    ("sonda", "El paquete se nombra por su contenido, no por su fecha",
     "Dos paquetes con el mismo nombre y distinto contenido convierten cualquier comparación "
     "en una discusión. El nombre sale del digest de lo que hay dentro.\n\nAsí dos máquinas "
     "dicen «el mismo paquete» y significan lo mismo.", ["formato", "evidencia"], {}, 14),
    ("nucleo", "Una migración corre una vez, y la tabla de aplicadas es quien lo decide",
     "El nombre ordena; la tabla decide. Sin la tabla, un despliegue que reintenta vuelve a "
     "correr la migración y duplica lo que ya insertó.\n\nUna migración que no se puede "
     "correr dos veces sin daño no está terminada.",
     ["migracion", "plataforma"], {"superficie": "runtime"}, 2),
    ("personal", "Lo que se decide una vez no se decide cada semana",
     "Cada decisión repetida es una decisión que no se escribió. Si vuelve por tercera vez, se "
     "anota aquí y se cierra.", ["metodo"], {}, 40),
]

FUENTES = [
    ("correo-flota", "Correo · flota de calentamiento", "resend", "correo",
     {"proposito": "Rastro normalizado de todo el correo que sale y entra de la flota. Alimenta "
                   "el almacén; no abre tickets salvo rebote duro o queja.",
      "dominios": ["correo.mirador.mx", "avisos.abastos.mx", "hola.northwind.io", "notas.nucleo.dev"],
      "responsable": "dana", "retencion": "crudo indefinido"},
     "svix", "resend", 0),
    ("arnes-sonda", "Arnés de Sonda", "generico", "sonda",
     {"proposito": "Una fila por corrida de la suite de integración. Una corrida roja abre entrada.",
      "responsable": "agente-auditor", "contrato": "registro append-only, una fila por corrida"},
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
