#!/usr/bin/env python3
"""
Event Log — núcleo compartido: esquema, identidad, filtros y el log de eventos.

Tres decisiones que sostienen todo lo demás:

1. **La clave de génesis es inmutable.** Nace con la entrada y no cambia nunca:
   ni al renombrar, ni al moverla de espacio, ni al cerrarla. Es lo que permite
   que la misma cosa exista aquí, en un repo y en la nube sin duplicarse.

2. **El log de eventos es la fuente de verdad del monitor**, y su `seq` es
   monotónico. Un cursor sobre un entero no se rompe con relojes desfasados ni
   con empates de timestamp; uno sobre fechas sí.

3. **La identidad se nombra por contenido, no por etiqueta.** Cada entrada y
   cada evento llevan el sha256 de su forma canónica. Una etiqueta puede mentir
   sin que nadie lo note durante semanas; un digest no.
"""
import hashlib, json, os, re, secrets, sqlite3, datetime as dt

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, "events.db")

# ---------------------------------------------------------------- vocabulario ---
KINDS = ["ticket", "nota", "senal"]
STATUS = ["entrante", "aceptado", "en curso", "bloqueado", "en revision", "cerrado", "descartado"]
OPEN_STATUS = ["entrante", "aceptado", "en curso", "bloqueado", "en revision"]
PRIORITY = ["P0", "P1", "P2", "P3"]
ACTOR_KINDS = ["persona", "agente", "sistema", "externo"]
CAPABILITIES = ["observar", "reaccionar", "actuar"]

EVENT_KINDS = [
    "entrada.creada", "entrada.actualizada", "entrada.movida", "entrada.cerrada",
    "entrada.comentada", "entrada.etiquetada", "entrada.asignada",
    "entrada.reclamada", "entrada.avance", "entrada.liberada", "entrada.arriendo_vencido",
    "senal.ingerida", "fuente.registrada", "monitor.registrado", "monitor.visto",
]


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def genesis(prefix="ev"):
    """`ev-20260910-a3f91c`: legible por una persona, único sin coordinación."""
    return f"{prefix}-{dt.datetime.now(dt.timezone.utc):%Y%m%d}-{secrets.token_hex(3)}"


def digest_of(obj):
    """sha256 de la forma canónica. Mismo contenido -> mismo digest, siempre."""
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def token_hash(tok):
    return hashlib.sha256(("evlog.v1." + tok).encode("utf-8")).hexdigest()


def jload(s, default=None):
    if not s:
        return default if default is not None else {}
    try:
        return json.loads(s)
    except Exception:
        return default if default is not None else {}


def jdump(o):
    return json.dumps(o, ensure_ascii=False, sort_keys=True)


# -------------------------------------------------------------------- esquema ---
SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS spaces (
  key TEXT PRIMARY KEY, name TEXT, kind TEXT, color TEXT,
  schema_json TEXT,          -- campos extra que este espacio declara
  meta TEXT, created TEXT, active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS actors (
  key TEXT PRIMARY KEY, name TEXT, kind TEXT, handle TEXT, email TEXT,
  color TEXT, initials TEXT, meta TEXT, created TEXT, active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS entries (
  genesis TEXT PRIMARY KEY,
  kind TEXT NOT NULL, space TEXT NOT NULL,
  title TEXT NOT NULL, body TEXT DEFAULT '',
  status TEXT NOT NULL, priority TEXT DEFAULT 'P2',
  assignee TEXT, requester TEXT, blocked_by TEXT,
  labels TEXT DEFAULT '[]', refs TEXT DEFAULT '[]',
  meta TEXT DEFAULT '{}',            -- metadata extensible, por espacio o fuente
  source TEXT DEFAULT 'ui',
  parent TEXT,
  due TEXT,
  digest TEXT, version INTEGER DEFAULT 1,
  created TEXT, updated TEXT, closed_at TEXT,
  claimed_by TEXT, claim_expires TEXT, claim_note TEXT,
  sync_state TEXT DEFAULT 'pendiente'
);

-- historia estilo git: cada mutación deja la versión anterior completa
CREATE TABLE IF NOT EXISTS revisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  genesis TEXT, version INTEGER, digest TEXT,
  snapshot TEXT,             -- el contenido ANTES del cambio
  changed TEXT,              -- qué campos cambiaron
  actor TEXT, at TEXT, note TEXT
);

-- el log. `seq` es el cursor de todo monitor.
CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL, kind TEXT NOT NULL,
  genesis TEXT, space TEXT, actor TEXT,
  payload TEXT DEFAULT '{}', digest TEXT
);

CREATE TABLE IF NOT EXISTS monitors (
  key TEXT PRIMARY KEY, name TEXT, owner TEXT, kind TEXT DEFAULT 'shell',
  filter TEXT DEFAULT '{}', capabilities TEXT DEFAULT '["observar"]',
  cursor INTEGER DEFAULT 0, lease_seconds INTEGER DEFAULT 900,
  token_hash TEXT, created TEXT, last_seen TEXT, active INTEGER DEFAULT 1,
  delivered INTEGER DEFAULT 0, acted INTEGER DEFAULT 0, meta TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS sources (
  key TEXT PRIMARY KEY, name TEXT, kind TEXT, space TEXT,
  context TEXT DEFAULT '{}',      -- el contexto con el que se registra la fuente
  verify TEXT DEFAULT 'ninguna',  -- svix | hmac-sha256 | ninguna
  secret_hash TEXT, normalizer TEXT DEFAULT 'generico',
  crea_entrada INTEGER DEFAULT 0, -- ¿una señal de esta fuente abre una entrada?
  active INTEGER DEFAULT 1, created TEXT,
  recibidos INTEGER DEFAULT 0, rechazados INTEGER DEFAULT 0, ultimo TEXT,
  meta TEXT DEFAULT '{}'
);

-- crudo inmutable: nunca se reescribe, nunca se borra
CREATE TABLE IF NOT EXISTS raw_payloads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT, received_at TEXT, headers TEXT, body TEXT,
  digest TEXT, verified INTEGER DEFAULT 0, remote TEXT, nota TEXT
);

-- la capa normalizada: el almacén
CREATE TABLE IF NOT EXISTS facts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  raw_id INTEGER, source TEXT, space TEXT,
  occurred_at TEXT, ingested_at TEXT,
  domain TEXT, action TEXT,
  subject_type TEXT, subject_id TEXT,
  actor TEXT, target TEXT,
  attrs TEXT DEFAULT '{}', digest TEXT, seq INTEGER
);

-- cola durable hacia la nube; se drena en orden y nunca pierde el orden
CREATE TABLE IF NOT EXISTS outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT, entidad TEXT, clave TEXT, op TEXT,
  payload TEXT, digest TEXT,
  state TEXT DEFAULT 'pendiente',   -- pendiente | enviado | error
  attempts INTEGER DEFAULT 0, last_error TEXT, synced_at TEXT
);

CREATE INDEX IF NOT EXISTS ix_entries_space  ON entries(space);
CREATE INDEX IF NOT EXISTS ix_entries_status ON entries(status);
CREATE INDEX IF NOT EXISTS ix_entries_kind   ON entries(kind);
CREATE INDEX IF NOT EXISTS ix_entries_assig  ON entries(assignee);
CREATE INDEX IF NOT EXISTS ix_entries_upd    ON entries(updated);
CREATE INDEX IF NOT EXISTS ix_events_gen     ON events(genesis);
CREATE INDEX IF NOT EXISTS ix_events_kind    ON events(kind);
CREATE INDEX IF NOT EXISTS ix_facts_dom      ON facts(domain, action);
CREATE INDEX IF NOT EXISTS ix_facts_occ      ON facts(occurred_at);
CREATE INDEX IF NOT EXISTS ix_rev_gen        ON revisions(genesis);
CREATE INDEX IF NOT EXISTS ix_outbox_state   ON outbox(state, id);
"""


def connect(path=DB):
    c = sqlite3.connect(path, check_same_thread=False, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


def init(c):
    c.executescript(SCHEMA)
    c.commit()


# ------------------------------------------------------------------- entradas ---
CONTENT_FIELDS = ["kind", "space", "title", "body", "status", "priority", "assignee",
                  "requester", "blocked_by", "labels", "refs", "meta", "parent", "due"]


def content_digest(row):
    return digest_of({k: row.get(k) for k in CONTENT_FIELDS})


def emit(c, kind, *, genesis=None, space=None, actor=None, payload=None, at=None):
    """Escribe en el log y devuelve el `seq`. Todo cambio pasa por aquí.

    `at` existe para el sembrado: un log cuyos eventos dicen todos "ahora"
    no permite leer cuándo pasó nada, y la curva de actividad sale plana.
    """
    p = payload or {}
    at = at or now()
    d = digest_of({"kind": kind, "genesis": genesis, "space": space, "actor": actor, "payload": p})
    cur = c.execute(
        "INSERT INTO events (at,kind,genesis,space,actor,payload,digest) VALUES (?,?,?,?,?,?,?)",
        (at, kind, genesis, space, actor, jdump(p), d))
    seq = cur.lastrowid
    c.execute("INSERT INTO outbox (at,entidad,clave,op,payload,digest) VALUES (?,?,?,?,?,?)",
              (at, "events", str(seq), "insert",
               jdump({"seq": seq, "at": at, "kind": kind, "genesis": genesis,
                      "space": space, "actor": actor, "payload": p}), d))
    return seq


def put_entry(c, data, actor="sistema", source="ui"):
    """Crea una entrada. Devuelve (genesis, seq)."""
    g = data.get("genesis") or genesis()
    row = {
        "genesis": g,
        "kind": data.get("kind", "ticket"),
        "space": data.get("space", "general"),
        "title": (data.get("title") or "").strip(),
        "body": data.get("body") or "",
        "status": data.get("status") or "entrante",
        "priority": data.get("priority") or "P2",
        "assignee": data.get("assignee"),
        "requester": data.get("requester") or actor,
        "blocked_by": data.get("blocked_by"),
        "labels": data.get("labels") or [],
        "refs": data.get("refs") or [],
        "meta": data.get("meta") or {},
        "parent": data.get("parent"),
        "due": data.get("due"),
    }
    row["digest"] = content_digest(row)
    t = data.get("created") or now()
    c.execute("""INSERT INTO entries (genesis,kind,space,title,body,status,priority,assignee,
                 requester,blocked_by,labels,refs,meta,source,parent,due,digest,version,
                 created,updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)""",
              (g, row["kind"], row["space"], row["title"], row["body"], row["status"],
               row["priority"], row["assignee"], row["requester"], row["blocked_by"],
               jdump(row["labels"]), jdump(row["refs"]), jdump(row["meta"]), source,
               row["parent"], row["due"], row["digest"], t, t))
    seq = emit(c, "entrada.creada", genesis=g, space=row["space"], actor=actor, at=t,
               payload={"title": row["title"], "kind": row["kind"], "status": row["status"],
                        "priority": row["priority"], "assignee": row["assignee"],
                        "labels": row["labels"], "source": source, "digest": row["digest"]})
    c.execute("INSERT INTO outbox (at,entidad,clave,op,payload,digest) VALUES (?,?,?,?,?,?)",
              (t, "entries", g, "insert", jdump(row), row["digest"]))
    return g, seq


def update_entry(c, g, changes, actor="sistema", note=None, at=None):
    """Muta una entrada guardando la versión anterior completa. Devuelve (fila, seq)."""
    cur = c.execute("SELECT * FROM entries WHERE genesis=?", (g,)).fetchone()
    if not cur:
        return None, None
    before = dict(cur)
    for f in ("labels", "refs", "meta"):
        before[f] = jload(before[f], [] if f != "meta" else {})

    after = dict(before)
    changed = {}
    for k, v in changes.items():
        if k not in CONTENT_FIELDS or v is None:
            continue
        if after.get(k) != v:
            changed[k] = {"de": after.get(k), "a": v}
            after[k] = v
    if not changed:
        return before, None

    after["digest"] = content_digest(after)
    ver = int(before.get("version") or 1) + 1
    t = at or now()
    if after["status"] in ("cerrado", "descartado") and not before.get("closed_at"):
        closed = t
    elif after["status"] not in ("cerrado", "descartado"):
        closed = None
    else:
        closed = before.get("closed_at")

    c.execute("""UPDATE entries SET kind=?,space=?,title=?,body=?,status=?,priority=?,
                 assignee=?,requester=?,blocked_by=?,labels=?,refs=?,meta=?,parent=?,due=?,
                 digest=?,version=?,updated=?,closed_at=?,sync_state='pendiente'
                 WHERE genesis=?""",
              (after["kind"], after["space"], after["title"], after["body"], after["status"],
               after["priority"], after["assignee"], after["requester"], after["blocked_by"],
               jdump(after["labels"]), jdump(after["refs"]), jdump(after["meta"]),
               after["parent"], after["due"], after["digest"], ver, t, closed, g))
    c.execute("""INSERT INTO revisions (genesis,version,digest,snapshot,changed,actor,at,note)
                 VALUES (?,?,?,?,?,?,?,?)""",
              (g, before.get("version") or 1, before.get("digest"),
               jdump({k: before.get(k) for k in CONTENT_FIELDS}), jdump(changed), actor, t, note))

    # el tipo de evento lo decide QUÉ cambió, no quién llamó. Emitir además un
    # evento propio por etiquetas dejaba dos líneas de log para un solo cambio.
    ev = ("entrada.cerrada" if after["status"] in ("cerrado", "descartado") else
          "entrada.movida" if "status" in changed else
          "entrada.asignada" if "assignee" in changed else
          "entrada.etiquetada" if set(changed) == {"labels"} else
          "entrada.actualizada")
    seq = emit(c, ev, genesis=g, space=after["space"], actor=actor, at=t,
               payload={"cambios": changed, "version": ver, "digest": after["digest"],
                        "title": after["title"], "status": after["status"],
                        "assignee": after["assignee"], "nota": note})
    c.execute("INSERT INTO outbox (at,entidad,clave,op,payload,digest) VALUES (?,?,?,?,?,?)",
              (t, "entries", g, "update", jdump(after), after["digest"]))
    after["version"] = ver
    return after, seq


# -------------------------------------------------------------------- filtros ---
def _as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def matches(filt, ev, entry=None):
    """Evalúa el filtro declarativo contra un evento (y su entrada, si existe).

    Un filtro vacío deja pasar todo. Cada clave presente ACOTA; entre claves es
    AND, dentro de una clave es OR. Sin negaciones implícitas: lo que no se
    nombra, no se filtra.
    """
    if not filt:
        return True
    e = entry or {}

    def val(k):
        # Los campos de entrada se leen de la ENTRADA. Leerlos del evento hacía
        # que un filtro `kind: ticket` comparara contra `entrada.creada`, que es
        # el tipo del evento, y no casaba nunca.
        if k in e and e.get(k) is not None:
            return e.get(k)
        return (ev.get("payload") or {}).get(k)

    for key, col in (("event", "kind"), ("space", "space"), ("actor", "actor")):
        want = _as_list(filt.get(key))
        if want:
            got = ev.get(col)
            if key == "event":
                # 'entrada.*' funciona como prefijo
                if not any(got == w or (w.endswith("*") and (got or "").startswith(w[:-1]))
                           for w in want):
                    return False
            elif got not in want:
                return False

    for key, col in (("kind", "kind"), ("status", "status"), ("priority", "priority"),
                     ("assignee", "assignee"), ("requester", "requester")):
        want = _as_list(filt.get(key))
        if want and val(col) not in want:
            return False

    if filt.get("abiertos") and val("status") not in OPEN_STATUS:
        return False

    want = _as_list(filt.get("labels_any"))
    if want:
        have = e.get("labels") or ev.get("payload", {}).get("labels") or []
        if not set(want) & set(have):
            return False
    want = _as_list(filt.get("labels_all"))
    if want:
        have = e.get("labels") or ev.get("payload", {}).get("labels") or []
        if not set(want) <= set(have):
            return False

    for mk, mv in (filt.get("meta") or {}).items():
        have = (e.get("meta") or {}).get(mk)
        if have not in _as_list(mv):
            return False

    txt = filt.get("text")
    if txt:
        hay = " ".join(str(x) for x in [
            e.get("title"), e.get("body"), ev.get("genesis"),
            jdump(ev.get("payload") or {}), jdump(e.get("meta") or {})]).lower()
        if txt.lower() not in hay:
            return False

    dom = _as_list(filt.get("domain"))
    if dom and (ev.get("payload") or {}).get("domain") not in dom:
        return False
    act = _as_list(filt.get("action"))
    if act and (ev.get("payload") or {}).get("action") not in act:
        return False
    return True


def valid_filter(f):
    """Rechaza claves desconocidas: un filtro con un typo silencioso no filtra
    nada y el monitor cree que no hay trabajo."""
    known = {"event", "space", "actor", "kind", "status", "priority", "assignee",
             "requester", "labels_any", "labels_all", "meta", "text", "abiertos",
             "domain", "action"}
    bad = set(f or {}) - known
    return (not bad), sorted(bad)
