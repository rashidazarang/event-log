#!/usr/bin/env python3
"""
Event Log — servidor. Interfaz, intake, webhooks y el bus de monitores.

    python3 server.py [--port 8124]

Sólo librería estándar. El estado vive en `events.db`; la cola hacia la nube en
la tabla `outbox`, que se drena en orden.
"""
import argparse, base64, hashlib, hmac, json, os, re, secrets, threading, time
import urllib.request, urllib.error, datetime as dt
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import core
from core import (jload, jdump, now, matches, valid_filter, emit, put_entry,
                  update_entry, token_hash, genesis, OPEN_STATUS, STATUS, PRIORITY)

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")
MIME = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".js": "text/javascript; charset=utf-8", ".json": "application/json",
        ".svg": "image/svg+xml", ".md": "text/markdown; charset=utf-8"}

DB_LOCK = threading.Lock()
LISTFIELDS = ("labels", "refs")


# ------------------------------------------------------------------ utilería ---
def rows(c, sql, args=()):
    return [dict(r) for r in c.execute(sql, args).fetchall()]


def one(c, sql, args=()):
    r = c.execute(sql, args).fetchone()
    return dict(r) if r else None


def scalar(c, sql, args=(), default=0):
    r = c.execute(sql, args).fetchone()
    return r[0] if r and r[0] is not None else default


def hydrate(e):
    """Devuelve la fila con sus campos JSON ya decodificados."""
    if not e:
        return e
    for f in LISTFIELDS:
        e[f] = jload(e.get(f), [])
    e["meta"] = jload(e.get("meta"), {})
    return e


def filter_from_query(q):
    """Traduce la query string al mismo filtro declarativo que usan los monitores.
    Una sola gramática para la interfaz y para el bus: si divergen, el monitor
    ve un mundo distinto del que ve la persona que lo configuró."""
    f = {}
    for k in ("event", "space", "kind", "status", "priority", "assignee", "requester",
              "labels_any", "labels_all", "actor", "domain", "action"):
        v = [x for x in q.get(k, []) if x]
        if v:
            f[k] = v
    if q.get("text"):
        f["text"] = q["text"][0]
    if q.get("abiertos") == ["1"]:
        f["abiertos"] = True
    meta = {}
    for key, vals in q.items():
        if key.startswith("meta."):
            meta[key[5:]] = vals
    if meta:
        f["meta"] = meta
    return f


def entry_where(f):
    """Parte del filtro que SQL puede resolver. El resto lo termina `matches`."""
    sql, args = [], []
    for key, col in (("space", "space"), ("kind", "kind"), ("status", "status"),
                     ("priority", "priority"), ("assignee", "assignee"),
                     ("requester", "requester")):
        v = f.get(key)
        if v:
            sql.append(f"{col} IN ({','.join('?' * len(v))})")
            args += v
    if f.get("abiertos"):
        sql.append(f"status IN ({','.join('?' * len(OPEN_STATUS))})")
        args += OPEN_STATUS
    if f.get("text"):
        sql.append("(title LIKE ? OR body LIKE ? OR genesis LIKE ? OR meta LIKE ?)")
        args += [f"%{f['text']}%"] * 4
    return (" AND ".join(sql) or "1=1"), args


def post_filter(f, items):
    """Etiquetas y metadata se evalúan en Python: están en JSON y un LIKE sobre
    JSON encuentra subcadenas que no son el valor."""
    if not any(k in f for k in ("labels_any", "labels_all", "meta")):
        return items
    return [i for i in items if matches(
        {k: f[k] for k in ("labels_any", "labels_all", "meta") if k in f},
        {"kind": None}, i)]


# ------------------------------------------------------------- arrendamientos ---
def sweep_leases(c, actor="sistema"):
    """Un reclamo vencido vuelve a la cola. Sin esto, un monitor que muere se
    lleva el trabajo consigo y nadie lo nota hasta que alguien pregunta."""
    t = now()
    vencidos = rows(c, "SELECT genesis, space, claimed_by FROM entries "
                       "WHERE claimed_by IS NOT NULL AND claim_expires IS NOT NULL "
                       "AND claim_expires < ?", (t,))
    for v in vencidos:
        c.execute("UPDATE entries SET claimed_by=NULL, claim_expires=NULL, claim_note=NULL "
                  "WHERE genesis=?", (v["genesis"],))
        emit(c, "entrada.arriendo_vencido", genesis=v["genesis"], space=v["space"],
             actor=actor, payload={"era_de": v["claimed_by"]})
    if vencidos:
        c.commit()
    return len(vencidos)


# ---------------------------------------------------------------- webhooks -----
def secret_for(key):
    return os.environ.get("EVLOG_SECRET_" + re.sub(r"[^A-Z0-9]", "_", key.upper()))


def verify_signature(src, headers, body):
    """(ok, motivo). Falla cerrado: sin secreto configurado no se acepta nada
    de una fuente que declaró verificación."""
    modo = src["verify"]
    if modo == "ninguna":
        return True, "sin verificación declarada"
    sec = secret_for(src["key"])
    if not sec:
        return False, f"falta el secreto en EVLOG_SECRET_{re.sub(r'[^A-Z0-9]', '_', src['key'].upper())}"
    if modo == "svix":
        sid = headers.get("svix-id", "")
        stamp = headers.get("svix-timestamp", "")
        sigs = headers.get("svix-signature", "")
        if not (sid and stamp and sigs):
            return False, "faltan encabezados svix"
        try:
            key = base64.b64decode(sec.split("_", 1)[1] if sec.startswith("whsec_") else sec)
        except Exception:
            return False, "secreto svix mal formado"
        mac = hmac.new(key, f"{sid}.{stamp}.{body}".encode(), hashlib.sha256).digest()
        esperado = base64.b64encode(mac).decode()
        for part in sigs.split():
            if "," in part and hmac.compare_digest(part.split(",", 1)[1], esperado):
                return True, "svix"
        return False, "firma svix no coincide"
    if modo == "hmac-sha256":
        got = headers.get("x-signature", "") or headers.get("x-hub-signature-256", "")
        got = got.split("=", 1)[-1]
        esperado = hmac.new(sec.encode(), body.encode(), hashlib.sha256).hexdigest()
        return (hmac.compare_digest(got, esperado), "hmac-sha256")
    return False, f"modo de verificación desconocido: {modo}"


def normalize(src, payload):
    """Devuelve el hecho canónico. Cada normalizador traduce el vocabulario de
    la fuente al del almacén; el crudo nunca se toca."""
    n = src["normalizer"]
    ctx = jload(src["context"], {})
    if n == "resend":
        tipo = (payload.get("type") or "").replace("email.", "")
        d = payload.get("data") or {}
        frm = d.get("from") or ""
        to = (d.get("to") or [""])[0]
        dom = frm.split("@")[-1] if "@" in frm else ""
        return {"domain": "email", "action": tipo or "desconocido",
                "subject_type": "email", "subject_id": d.get("email_id") or "",
                "actor": frm, "target": to,
                "occurred_at": payload.get("created_at") or now(),
                "attrs": {"dominio": dom, "buzon": frm.split("@")[0] if "@" in frm else "",
                          "destino": to.split("@")[-1] if "@" in to else "",
                          "asunto": d.get("subject") or "", "proveedor": "resend",
                          "flota": "warming" if dom in (ctx.get("dominios") or []) else "otro"}}
    if n == "intake":
        return {"domain": "intake", "action": "recibido", "subject_type": "solicitud",
                "subject_id": payload.get("id") or "", "actor": payload.get("requester") or "",
                "target": payload.get("space") or "", "occurred_at": now(),
                "attrs": payload}
    # genérico: se conserva todo, y se buscan las claves que casi todos usan
    accion = payload.get("action") or payload.get("event") or payload.get("type") or "evento"
    return {"domain": src["kind"] or "generico", "action": str(accion),
            "subject_type": payload.get("subject_type") or "objeto",
            "subject_id": str(payload.get("id") or payload.get("subject_id") or ""),
            "actor": str(payload.get("actor") or payload.get("sender") or ""),
            "target": str(payload.get("target") or ""),
            "occurred_at": payload.get("occurred_at") or payload.get("created_at") or now(),
            "attrs": payload}


REGLAS_ENTRADA = {
    # (dominio, acción) -> (prioridad, plantilla de título, etiquetas)
    ("email", "complained"): ("P0", "Queja de spam en {dominio} — revisar la flota",
                              ["resend", "reputacion", "automatico"]),
    ("email", "bounced"): ("P2", "Rebote duro en {dominio} hacia {destino}",
                           ["resend", "rebote", "automatico"]),
}


# ------------------------------------------------------------------ handler ----
class H(BaseHTTPRequestHandler):
    server_version = "Event Log/1.0"
    db = None

    def log_message(self, *a):
        if os.environ.get("EVLOG_VERBOSE"):
            super().log_message(*a)

    # ---- io
    def j(self, obj, code=200):
        body = jdump(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def text(self, s, code=200, ctype="text/plain; charset=utf-8", filename=None):
        body = s.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def raw_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n).decode("utf-8") if n else ""

    def body_json(self):
        try:
            return json.loads(self.raw_body() or "{}")
        except Exception:
            return {}

    def bearer(self):
        h = self.headers.get("Authorization") or ""
        return h[7:].strip() if h.lower().startswith("bearer ") else None

    def static(self, path):
        rel = "index.html" if path == "/" else path.lstrip("/")
        target = os.path.normpath(os.path.join(WEB, rel))
        if not target.startswith(WEB):
            return self.j({"error": "prohibido"}, 403)
        if not os.path.isfile(target):
            target = os.path.join(WEB, "index.html")
        with open(target, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(os.path.splitext(target)[1], "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # ============================================================== GET =======
    def do_GET(self):
        u = urlparse(self.path)
        p, q = u.path, parse_qs(u.query)
        c = self.db
        try:
            if p.startswith("/api/"):
                with DB_LOCK:
                    sweep_leases(c)
                return self.route_get(p, q, c)
        except Exception as e:
            return self.j({"error": f"{type(e).__name__}: {e}"}, 500)
        return self.static(p)

    def route_get(self, p, q, c):
        if p == "/api/bootstrap":
            return self.j(self.bootstrap(c))
        if p == "/api/entries":
            return self.j(self.entries(c, q))
        if p == "/api/board":
            return self.j(self.board(c, q))
        if p == "/api/feed":
            return self.j(self.feed(c, q))
        if p == "/api/facts":
            return self.j(self.facts(c, q))
        if p == "/api/stats":
            return self.j(self.stats(c, q))
        if p == "/api/sources":
            return self.j({"items": [self.pub_source(s) for s in
                                     rows(c, "SELECT * FROM sources ORDER BY key")]})
        if p == "/api/monitors":
            return self.j({"items": [self.pub_monitor(c, m) for m in
                                     rows(c, "SELECT * FROM monitors ORDER BY key")]})
        if p == "/api/sync":
            return self.j(self.sync_status(c))
        m = re.fullmatch(r"/api/entries/(ev-[\w-]+)", p)
        if m:
            d = self.detail(c, m.group(1))
            return self.j(d or {"error": "no existe"}, 200 if d else 404)
        m = re.fullmatch(r"/api/raw/(\d+)", p)
        if m:
            r = one(c, "SELECT * FROM raw_payloads WHERE id=?", (int(m.group(1)),))
            if r:
                r["headers"] = jload(r["headers"], {})
            return self.j(r or {"error": "no existe"}, 200 if r else 404)
        m = re.fullmatch(r"/api/monitors/([\w.-]+)/feed", p)
        if m:
            return self.monitor_feed(c, m.group(1), q)
        if p == "/api/export/facts.csv":
            return self.export_facts(c, q)
        return self.j({"error": "ruta desconocida"}, 404)

    # ============================================================ payloads ====
    def bootstrap(self, c):
        return {
            "ahora": now(),
            # En la demostración desplegada cada instancia arranca de una semilla
            # y sus escrituras se pierden al reciclarse. Decirlo es parte del
            # producto: una demo que finge ser producción enseña mal.
            "demo": bool(os.environ.get("EVLOG_DEMO")),
            "spaces": [dict(s, schema_json=jload(s["schema_json"], {}))
                       for s in rows(c, "SELECT * FROM spaces WHERE active=1 ORDER BY key")],
            "actors": rows(c, "SELECT * FROM actors WHERE active=1 ORDER BY kind, name"),
            "status": STATUS, "abiertos": OPEN_STATUS, "priority": PRIORITY,
            "kinds": ["ticket", "nota"],
            "eventKinds": core.EVENT_KINDS,
            "capabilities": core.CAPABILITIES,
            "totales": {
                "entradas": scalar(c, "SELECT COUNT(*) FROM entries"),
                "abiertas": scalar(c, f"SELECT COUNT(*) FROM entries WHERE status IN "
                                      f"({','.join('?' * len(OPEN_STATUS))})", OPEN_STATUS),
                "notas": scalar(c, "SELECT COUNT(*) FROM entries WHERE kind='nota'"),
                "senales": scalar(c, "SELECT COUNT(*) FROM facts"),
                "eventos": scalar(c, "SELECT COALESCE(MAX(seq),0) FROM events"),
                "monitores": scalar(c, "SELECT COUNT(*) FROM monitors WHERE active=1"),
                "fuentes": scalar(c, "SELECT COUNT(*) FROM sources WHERE active=1"),
                "outbox": scalar(c, "SELECT COUNT(*) FROM outbox WHERE state='pendiente'"),
            },
            "facetas": {
                "space": rows(c, "SELECT space value, COUNT(*) n FROM entries GROUP BY space ORDER BY n DESC"),
                "status": rows(c, "SELECT status value, COUNT(*) n FROM entries GROUP BY status"),
                "priority": rows(c, "SELECT priority value, COUNT(*) n FROM entries GROUP BY priority ORDER BY value"),
                "assignee": rows(c, "SELECT assignee value, COUNT(*) n FROM entries WHERE assignee IS NOT NULL GROUP BY assignee ORDER BY n DESC"),
                "labels": self.label_facets(c),
            },
        }

    def label_facets(self, c):
        cnt = {}
        for r in c.execute("SELECT labels FROM entries"):
            for l in jload(r[0], []):
                cnt[l] = cnt.get(l, 0) + 1
        return [{"value": k, "n": v} for k, v in sorted(cnt.items(), key=lambda x: -x[1])]

    def entries(self, c, q):
        f = filter_from_query(q)
        w, a = entry_where(f)
        orden = {"actualizado": "updated", "creado": "created", "prioridad": "priority",
                 "titulo": "title", "espacio": "space"}.get(q.get("sort", ["actualizado"])[0], "updated")
        dirn = "ASC" if q.get("dir", ["desc"])[0] == "asc" else "DESC"
        items = [hydrate(e) for e in rows(c, f"SELECT * FROM entries WHERE {w} "
                                             f"ORDER BY {orden} {dirn}, genesis", a)]
        items = post_filter(f, items)
        page = max(1, int(q.get("page", ["1"])[0]))
        per = min(200, max(5, int(q.get("per", ["40"])[0])))
        total = len(items)
        return {"items": items[(page - 1) * per: page * per], "total": total,
                "page": page, "per": per, "pages": max(1, -(-total // per)), "filtro": f}

    def board(self, c, q):
        f = filter_from_query(q)
        w, a = entry_where(f)
        allitems = post_filter(f, [hydrate(e) for e in rows(
            c, f"SELECT * FROM entries WHERE {w} AND kind='ticket' ORDER BY priority, updated DESC", a)])
        cols = []
        for s in STATUS:
            sel = [i for i in allitems if i["status"] == s]
            cols.append({"status": s, "count": len(sel), "items": sel[:25]})
        return {"columns": cols, "total": len(allitems)}

    def feed(self, c, q):
        since = int(q.get("since", ["0"])[0])
        limit = min(500, max(1, int(q.get("limit", ["60"])[0])))
        f = filter_from_query(q)
        evs, head = self.read_feed(c, since, limit, f)
        return {"events": evs, "cursor": (evs[-1]["seq"] if evs else since), "head": head,
                "filtro": f}

    def read_feed(self, c, since, limit, f):
        """Lee el log desde el cursor y aplica el filtro con la entrada a la vista.
        El `head` siempre es el máximo real del log, no el del tramo filtrado: un
        monitor que avanza su cursor al último *entregado* se queda atrás para
        siempre cuando su filtro descarta la cola."""
        head = scalar(c, "SELECT COALESCE(MAX(seq),0) FROM events")
        out, scan, cursor = [], 0, since
        while len(out) < limit and cursor < head and scan < 8000:
            lote = rows(c, "SELECT * FROM events WHERE seq>? ORDER BY seq LIMIT 400", (cursor,))
            if not lote:
                break
            for ev in lote:
                scan += 1
                cursor = ev["seq"]
                ev["payload"] = jload(ev["payload"], {})
                ent = None
                if ev["genesis"]:
                    ent = hydrate(one(c, "SELECT * FROM entries WHERE genesis=?", (ev["genesis"],)))
                if matches(f, ev, ent):
                    ev["entrada"] = {k: ent.get(k) for k in
                                     ("genesis", "kind", "title", "status", "priority",
                                      "assignee", "space", "labels", "claimed_by")} if ent else None
                    out.append(ev)
                    if len(out) >= limit:
                        break
        return out, head

    def facts(self, c, q):
        w, a = ["1=1"], []
        for k, col in (("source", "source"), ("space", "space"), ("domain", "domain"),
                       ("action", "action")):
            v = [x for x in q.get(k, []) if x]
            if v:
                w.append(f"{col} IN ({','.join('?' * len(v))})")
                a += v
        if q.get("desde"):
            w.append("occurred_at >= ?"); a.append(q["desde"][0])
        if q.get("text"):
            w.append("(subject_id LIKE ? OR actor LIKE ? OR target LIKE ? OR attrs LIKE ?)")
            a += [f"%{q['text'][0]}%"] * 4
        ws = " AND ".join(w)
        limit = min(500, max(1, int(q.get("limit", ["60"])[0])))
        items = rows(c, f"SELECT * FROM facts WHERE {ws} ORDER BY occurred_at DESC, id DESC "
                        f"LIMIT ?", a + [limit])
        for i in items:
            i["attrs"] = jload(i["attrs"], {})
        serie = rows(c, f"SELECT substr(occurred_at,1,10) d, action, COUNT(*) n "
                        f"FROM facts WHERE {ws} GROUP BY d, action ORDER BY d", a)
        return {
            "items": items,
            "total": scalar(c, f"SELECT COUNT(*) FROM facts WHERE {ws}", a),
            "porAccion": rows(c, f"SELECT action value, COUNT(*) n FROM facts WHERE {ws} "
                                 f"GROUP BY action ORDER BY n DESC", a),
            "porDominio": rows(c, f"SELECT domain value, COUNT(*) n FROM facts WHERE {ws} "
                                  f"GROUP BY domain ORDER BY n DESC", a),
            "porFuente": rows(c, f"SELECT source value, COUNT(*) n FROM facts WHERE {ws} "
                                 f"GROUP BY source ORDER BY n DESC", a),
            "porEmisor": rows(c, f"SELECT json_extract(attrs,'$.dominio') value, COUNT(*) n "
                                 f"FROM facts WHERE {ws} AND json_extract(attrs,'$.dominio') IS NOT NULL "
                                 f"GROUP BY value ORDER BY n DESC LIMIT 12", a),
            "serie": serie,
        }

    def stats(self, c, q):
        f = filter_from_query(q)
        w, a = entry_where(f)
        por = lambda col: rows(c, f"SELECT {col} value, COUNT(*) n FROM entries WHERE {w} "
                                  f"GROUP BY {col} ORDER BY n DESC", a)
        abiertas = scalar(c, f"SELECT COUNT(*) FROM entries WHERE {w} AND status IN "
                             f"({','.join('?' * len(OPEN_STATUS))})", a + OPEN_STATUS)
        # actividad del log por día
        serie = rows(c, "SELECT substr(at,1,10) d, COUNT(*) n FROM events "
                        "GROUP BY d ORDER BY d DESC LIMIT 21")
        return {
            "abiertas": abiertas,
            "total": scalar(c, f"SELECT COUNT(*) FROM entries WHERE {w}", a),
            "porEspacio": por("space"), "porEstado": por("status"),
            "porPrioridad": por("priority"), "porResponsable": por("assignee"),
            "porTipo": por("kind"),
            "serie": list(reversed(serie)),
            "sinAsignar": scalar(c, f"SELECT COUNT(*) FROM entries WHERE {w} AND assignee IS NULL "
                                    f"AND status IN ({','.join('?' * len(OPEN_STATUS))})", a + OPEN_STATUS),
            "reclamadas": scalar(c, f"SELECT COUNT(*) FROM entries WHERE {w} AND claimed_by IS NOT NULL", a),
            "senales": scalar(c, "SELECT COUNT(*) FROM facts"),
            "eventos": scalar(c, "SELECT COALESCE(MAX(seq),0) FROM events"),
        }

    def detail(self, c, g):
        e = hydrate(one(c, "SELECT * FROM entries WHERE genesis=?", (g,)))
        if not e:
            return None
        e["revisiones"] = [dict(r, snapshot=jload(r["snapshot"], {}), changed=jload(r["changed"], {}))
                           for r in rows(c, "SELECT * FROM revisions WHERE genesis=? "
                                            "ORDER BY version DESC", (g,))]
        e["eventos"] = [dict(ev, payload=jload(ev["payload"], {})) for ev in
                        rows(c, "SELECT * FROM events WHERE genesis=? ORDER BY seq DESC", (g,))]
        e["hijos"] = rows(c, "SELECT genesis,title,status,kind FROM entries WHERE parent=?", (g,))
        esp = one(c, "SELECT * FROM spaces WHERE key=?", (e["space"],))
        e["espacio"] = dict(esp, schema_json=jload(esp["schema_json"], {})) if esp else None
        return e

    def pub_source(self, s):
        s = dict(s, context=jload(s["context"], {}), meta=jload(s["meta"], {}))
        s.pop("secret_hash", None)
        var = "EVLOG_SECRET_" + re.sub(r"[^A-Z0-9]", "_", s["key"].upper())
        s["secreto_env"] = var
        s["secreto_presente"] = bool(os.environ.get(var))
        s["endpoint"] = f"/api/hooks/{s['key']}"
        return s

    def pub_monitor(self, c, m):
        m = dict(m, filter=jload(m["filter"], {}), capabilities=jload(m["capabilities"], []),
                 meta=jload(m["meta"], {}))
        m.pop("token_hash", None)
        head = scalar(c, "SELECT COALESCE(MAX(seq),0) FROM events")
        m["head"] = head
        m["atraso"] = max(0, head - (m["cursor"] or 0))
        m["reclamadas"] = scalar(c, "SELECT COUNT(*) FROM entries WHERE claimed_by=?", (m["key"],))
        return m

    def sync_status(self, c):
        return {
            "destino": "neon" if os.environ.get("EVLOG_NEON_URL") else None,
            "configurado": bool(os.environ.get("EVLOG_NEON_URL") and os.environ.get("EVLOG_NEON_CONN")),
            "pendiente": scalar(c, "SELECT COUNT(*) FROM outbox WHERE state='pendiente'"),
            "enviado": scalar(c, "SELECT COUNT(*) FROM outbox WHERE state='enviado'"),
            "error": scalar(c, "SELECT COUNT(*) FROM outbox WHERE state='error'"),
            "ultimo": scalar(c, "SELECT MAX(synced_at) FROM outbox", default=None),
            "porEntidad": rows(c, "SELECT entidad value, state, COUNT(*) n FROM outbox "
                                  "GROUP BY entidad, state"),
            "muestra": rows(c, "SELECT id,at,entidad,clave,op,digest,state,attempts,last_error "
                               "FROM outbox WHERE state='pendiente' ORDER BY id LIMIT 12"),
        }

    def export_facts(self, c, q):
        cols = ["occurred_at", "source", "space", "domain", "action", "subject_id",
                "actor", "target", "digest"]
        out = [",".join(cols)]
        w = "1=1"
        a = []
        if q.get("domain"):
            w += " AND domain=?"; a.append(q["domain"][0])
        for r in rows(c, f"SELECT * FROM facts WHERE {w} ORDER BY occurred_at DESC LIMIT 20000", a):
            out.append(",".join('"' + str(r[k] or "").replace('"', '""') + '"' for k in cols))
        return self.text("\n".join(out), ctype="text/csv; charset=utf-8", filename="senales.csv")

    # ============================================================= POST ======
    def do_POST(self):
        u = urlparse(self.path)
        p = u.path
        c = self.db
        try:
            with DB_LOCK:
                sweep_leases(c)
                return self.route_post(p, parse_qs(u.query), c)
        except Exception as e:
            return self.j({"error": f"{type(e).__name__}: {e}"}, 500)

    def route_post(self, p, q, c):
        if p == "/api/entries":
            return self.crear(c)
        if p == "/api/intake":
            return self.intake(c)
        if p == "/api/monitors":
            return self.registrar_monitor(c)
        if p == "/api/sync/drain":
            return self.drain(c)
        m = re.fullmatch(r"/api/entries/(ev-[\w-]+)/comentario", p)
        if m:
            return self.comentar(c, m.group(1))
        m = re.fullmatch(r"/api/hooks/([\w.-]+)", p)
        if m:
            return self.hook(c, m.group(1))
        m = re.fullmatch(r"/api/monitors/([\w.-]+)/(ack|claim|progress|complete|release|retire|annotate)", p)
        if m:
            return self.monitor_action(c, m.group(1), m.group(2))
        return self.j({"error": "ruta desconocida"}, 404)

    def do_PATCH(self):
        m = re.fullmatch(r"/api/entries/(ev-[\w-]+)", urlparse(self.path).path)
        if not m:
            return self.j({"error": "ruta desconocida"}, 404)
        b = self.body_json()
        actor = b.pop("actor", None) or "dana"
        nota = b.pop("nota", None)
        try:
            with DB_LOCK:
                e, seq = update_entry(c := self.db, m.group(1), b, actor=actor, note=nota)
                if e is None:
                    return self.j({"error": "no existe"}, 404)
                c.commit()
            return self.j({"entrada": self.detail(self.db, m.group(1)), "seq": seq})
        except Exception as e:
            return self.j({"error": f"{type(e).__name__}: {e}"}, 500)

    # ---- escritura desde la interfaz
    def crear(self, c):
        b = self.body_json()
        if not (b.get("title") or "").strip():
            return self.j({"error": "el título es obligatorio"}, 400)
        actor = b.get("actor") or b.get("requester") or "dana"
        g, seq = put_entry(c, b, actor=actor, source=b.get("source") or "ui")
        c.commit()
        return self.j({"genesis": g, "seq": seq, "entrada": self.detail(c, g)}, 201)

    def comentar(self, c, g):
        b = self.body_json()
        txt = (b.get("texto") or "").strip()
        if not txt:
            return self.j({"error": "comentario vacío"}, 400)
        e = one(c, "SELECT space FROM entries WHERE genesis=?", (g,))
        if not e:
            return self.j({"error": "no existe"}, 404)
        seq = emit(c, "entrada.comentada", genesis=g, space=e["space"],
                   actor=b.get("actor") or "dana", payload={"texto": txt})
        c.execute("UPDATE entries SET updated=? WHERE genesis=?", (now(), g))
        c.commit()
        return self.j({"seq": seq, "entrada": self.detail(c, g)})

    def intake(self, c):
        """Puerta pública: cualquiera con el enlace deja una tarea. Siempre entra
        como `entrante` y siempre declara quién la pide."""
        src = one(c, "SELECT * FROM sources WHERE key='intake-publico' AND active=1")
        if not src:
            return self.j({"error": "el intake está cerrado"}, 403)
        b = self.body_json()
        title = (b.get("title") or b.get("titulo") or "").strip()
        if not title:
            return self.j({"error": "el título es obligatorio"}, 400)
        req = (b.get("requester") or b.get("solicitante") or "externo").strip()
        raw = jdump(b)
        c.execute("""INSERT INTO raw_payloads (source,received_at,headers,body,digest,verified,remote)
                     VALUES (?,?,?,?,?,?,?)""",
                  ("intake-publico", now(), jdump(dict(self.headers)), raw,
                   hashlib.sha256(raw.encode()).hexdigest(), 1,
                   self.client_address[0] if self.client_address else ""))
        g, seq = put_entry(c, {
            "kind": "ticket", "space": b.get("space") or "general",
            "title": title, "body": b.get("body") or b.get("cuerpo") or "",
            "status": "entrante", "priority": b.get("priority") or "P2",
            "requester": req, "labels": (b.get("labels") or []) + ["intake"],
            "meta": dict(b.get("meta") or {}, canal="intake",
                         contacto=b.get("contacto") or ""),
        }, actor=req, source="intake")
        c.execute("UPDATE sources SET recibidos=recibidos+1, ultimo=? WHERE key='intake-publico'",
                  (now(),))
        c.commit()
        return self.j({"genesis": g, "seq": seq,
                       "mensaje": "Recibido. Queda como entrante hasta que alguien lo acepte."}, 201)

    # ---- webhooks
    def hook(self, c, key):
        src = one(c, "SELECT * FROM sources WHERE key=? AND active=1", (key,))
        if not src:
            return self.j({"error": "fuente no registrada"}, 404)
        body = self.raw_body()
        heads = {k.lower(): v for k, v in self.headers.items()}
        ok, motivo = verify_signature(src, heads, body)
        d = hashlib.sha256(body.encode()).hexdigest()
        c.execute("""INSERT INTO raw_payloads (source,received_at,headers,body,digest,verified,
                     remote,nota) VALUES (?,?,?,?,?,?,?,?)""",
                  (key, now(), jdump(heads), body, d, 1 if ok else 0,
                   self.client_address[0] if self.client_address else "", motivo))
        raw_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        if not ok:
            c.execute("UPDATE sources SET rechazados=rechazados+1, ultimo=? WHERE key=?",
                      (now(), key))
            c.commit()
            # el crudo se conserva aunque la firma falle: un rechazo es evidencia
            return self.j({"error": "firma inválida", "motivo": motivo, "raw_id": raw_id}, 401)
        try:
            payload = json.loads(body or "{}")
        except Exception:
            c.commit()
            return self.j({"error": "JSON inválido", "raw_id": raw_id}, 400)

        f = normalize(src, payload)
        seq = emit(c, "senal.ingerida", space=src["space"], actor=f"fuente:{key}",
                   payload={"fuente": key, "domain": f["domain"], "action": f["action"],
                            "subject_id": f["subject_id"], **f["attrs"]})
        fd = core.digest_of({"source": key, "domain": f["domain"], "action": f["action"],
                             "subject_id": f["subject_id"], "at": f["occurred_at"]})
        c.execute("""INSERT INTO facts (raw_id,source,space,occurred_at,ingested_at,domain,
                     action,subject_type,subject_id,actor,target,attrs,digest,seq)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (raw_id, key, src["space"], f["occurred_at"], now(), f["domain"], f["action"],
                   f["subject_type"], f["subject_id"], f["actor"], f["target"],
                   jdump(f["attrs"]), fd, seq))
        c.execute("INSERT INTO outbox (at,entidad,clave,op,payload,digest) VALUES (?,?,?,?,?,?)",
                  (now(), "facts", fd, "insert", jdump(dict(f, source=key)), fd))
        c.execute("UPDATE sources SET recibidos=recibidos+1, ultimo=? WHERE key=?", (now(), key))

        creada = None
        regla = REGLAS_ENTRADA.get((f["domain"], f["action"]))
        if regla or src["crea_entrada"]:
            prio, plantilla, labels = regla or (
                "P2", f"{f['domain']}.{f['action']} desde {key}", [key, "automatico"])
            try:
                titulo = plantilla.format(**{**f["attrs"], **f})
            except Exception:
                titulo = plantilla
            creada, _ = put_entry(c, {
                "kind": "ticket", "space": src["space"], "title": titulo,
                "body": f"Señal `{f['domain']}.{f['action']}` de `{key}`.\n\n"
                        f"Sujeto `{f['subject_id']}` · {f['actor']} → {f['target']}",
                "status": "entrante", "priority": prio, "labels": labels,
                "refs": [{"tipo": "senal", "valor": f["subject_id"]}],
                "meta": f["attrs"], "requester": f"fuente:{key}",
            }, actor=f"fuente:{key}", source=f"webhook:{key}")
        c.commit()
        return self.j({"ok": True, "seq": seq, "raw_id": raw_id, "hecho": f["action"],
                       "entrada_creada": creada})

    # ---- el bus de monitores
    def registrar_monitor(self, c):
        b = self.body_json()
        key = (b.get("key") or "").strip() or f"mon-{secrets.token_hex(3)}"
        if not re.fullmatch(r"[\w.-]{2,48}", key):
            return self.j({"error": "clave inválida"}, 400)
        filt = b.get("filter") or b.get("filtro") or {}
        ok, malas = valid_filter(filt)
        if not ok:
            return self.j({"error": f"claves de filtro desconocidas: {malas}",
                           "conocidas": sorted(["event", "space", "actor", "kind", "status",
                                                "priority", "assignee", "requester", "labels_any",
                                                "labels_all", "meta", "text", "abiertos",
                                                "domain", "action"])}, 400)
        caps = [x for x in (b.get("capabilities") or ["observar"]) if x in core.CAPABILITIES]
        existe = one(c, "SELECT key FROM monitors WHERE key=?", (key,))
        tok = secrets.token_urlsafe(24)
        desde = b.get("desde")
        head = scalar(c, "SELECT COALESCE(MAX(seq),0) FROM events")
        cursor = head if desde == "ahora" else int(b.get("cursor") or 0)
        if existe:
            c.execute("""UPDATE monitors SET name=?,owner=?,kind=?,filter=?,capabilities=?,
                         lease_seconds=?,token_hash=?,active=1,last_seen=? WHERE key=?""",
                      (b.get("name") or key, b.get("owner") or "dana", b.get("kind") or "shell",
                       jdump(filt), jdump(caps), int(b.get("lease_seconds") or 900),
                       token_hash(tok), now(), key))
        else:
            c.execute("""INSERT INTO monitors (key,name,owner,kind,filter,capabilities,cursor,
                         lease_seconds,token_hash,created,last_seen,meta)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (key, b.get("name") or key, b.get("owner") or "dana",
                       b.get("kind") or "shell", jdump(filt), jdump(caps), cursor,
                       int(b.get("lease_seconds") or 900), token_hash(tok), now(), now(),
                       jdump(b.get("meta") or {})))
        emit(c, "monitor.registrado", actor=b.get("owner") or "dana",
             payload={"monitor": key, "filtro": filt, "capacidades": caps,
                      "renovado": bool(existe)})
        c.commit()
        return self.j({"key": key, "token": tok, "cursor": cursor, "head": head,
                       "capacidades": caps, "filtro": filt,
                       "aviso": "El token se muestra una sola vez. Guárdalo."}, 201)

    def auth_monitor(self, c, key):
        """Devuelve el monitor, o None habiendo YA escrito la respuesta de error.
        Antes devolvía una tupla cuyo segundo miembro era el retorno de `self.j`,
        que es None siempre: el guardia no guardaba nada."""
        m = one(c, "SELECT * FROM monitors WHERE key=? AND active=1", (key,))
        if not m:
            self.j({"error": "monitor no registrado"}, 404)
            return None
        tok = self.bearer()
        abierto = os.environ.get("EVLOG_OPEN") == "1"
        if not abierto and (not tok or not hmac.compare_digest(token_hash(tok), m["token_hash"] or "")):
            self.j({"error": "token inválido o ausente",
                    "pista": "Authorization: Bearer <token>"}, 401)
            return None
        return m

    def monitor_feed(self, c, key, q):
        with DB_LOCK:
            m = self.auth_monitor(c, key)
        if m is None:
            return
        limit = min(200, max(1, int(q.get("limit", ["50"])[0])))
        wait = min(50, max(0, int(q.get("wait", ["0"])[0])))
        filt = jload(m["filter"], {})
        inline = filter_from_query(q)
        filt = {**filt, **inline}          # el llamador puede acotar más, nunca ampliar
        deadline = time.time() + wait
        while True:
            with DB_LOCK:
                evs, head = self.read_feed(c, m["cursor"] or 0, limit, filt)
                c.execute("UPDATE monitors SET last_seen=? WHERE key=?", (now(), key))
                c.commit()
            if evs or time.time() >= deadline:
                break
            time.sleep(0.5)
            with DB_LOCK:
                m = one(c, "SELECT * FROM monitors WHERE key=?", (key,))
        return self.j({
            "monitor": key, "cursor": m["cursor"] or 0, "head": head,
            "events": evs,
            "siguiente": evs[-1]["seq"] if evs else head,
            "atraso": max(0, head - (m["cursor"] or 0)),
            "capacidades": jload(m["capabilities"], []),
            "filtro": filt,
        })

    def monitor_action(self, c, key, action):
        m = self.auth_monitor(c, key)
        if m is None:
            return
        caps = jload(m["capabilities"], [])
        b = self.body_json()
        if action == "ack":
            cur = int(b.get("cursor") or 0)
            head = scalar(c, "SELECT COALESCE(MAX(seq),0) FROM events")
            cur = max(0, min(cur, head))
            c.execute("UPDATE monitors SET cursor=?, last_seen=?, delivered=delivered+? WHERE key=?",
                      (cur, now(), max(0, cur - (m["cursor"] or 0)), key))
            c.commit()
            return self.j({"cursor": cur, "head": head, "atraso": head - cur})
        if action == "retire":
            c.execute("UPDATE monitors SET active=0 WHERE key=?", (key,))
            c.execute("UPDATE entries SET claimed_by=NULL, claim_expires=NULL WHERE claimed_by=?", (key,))
            c.commit()
            return self.j({"ok": True, "retirado": key})

        # `annotate` es lo que el protocolo llama reaccionar: comentar y etiquetar.
        # Cambiar dueño o prioridad ya es actuar, y se exige aparte.
        if action == "annotate":
            if "reaccionar" not in caps:
                return self.j({"error": f"el monitor '{key}' no declaró 'reaccionar'",
                               "capacidades": caps}, 403)
            g = b.get("genesis")
            e = hydrate(one(c, "SELECT * FROM entries WHERE genesis=?", (g,))) if g else None
            if not e:
                return self.j({"error": "entrada no encontrada"}, 404)
            hechos, seqs = [], []
            if b.get("comentario"):
                seqs.append(emit(c, "entrada.comentada", genesis=g, space=e["space"], actor=key,
                                 payload={"texto": b["comentario"], "regla": b.get("regla")}))
                hechos.append("comentario")
            add = [l for l in (b.get("labels_add") or []) if l not in e["labels"]]
            quita = [l for l in (b.get("labels_del") or []) if l in e["labels"]]
            if add or quita:
                nuevas = [l for l in e["labels"] + add if l not in quita]
                _, sq = update_entry(c, g, {"labels": nuevas}, actor=key,
                                     note=b.get("regla"))   # ya emite entrada.etiquetada
                seqs.append(sq)
                hechos.append("etiquetas")
            cambio = {}
            for campo in ("assignee", "priority"):
                if b.get(campo) is not None and b.get(campo) != e.get(campo):
                    cambio[campo] = b[campo]
            if cambio:
                if "actuar" not in caps:
                    c.commit()
                    return self.j({"error": "cambiar dueño o prioridad exige 'actuar'",
                                   "capacidades": caps, "hecho": hechos}, 403)
                _, sq = update_entry(c, g, cambio, actor=key, note=b.get("regla"))
                seqs.append(sq)
                hechos.append("+".join(cambio))
            c.execute("UPDATE monitors SET last_seen=? WHERE key=?", (now(), key))
            c.commit()
            return self.j({"ok": True, "hecho": hechos, "seqs": [x for x in seqs if x],
                           "entrada": self.detail(c, g)})

        if "actuar" not in caps:
            return self.j({"error": f"el monitor '{key}' no declaró la capacidad 'actuar'",
                           "capacidades": caps}, 403)
        g = b.get("genesis")
        e = hydrate(one(c, "SELECT * FROM entries WHERE genesis=?", (g,))) if g else None
        if not e:
            return self.j({"error": "entrada no encontrada"}, 404)

        if action == "claim":
            lease = int(b.get("lease") or m["lease_seconds"] or 900)
            ahora = now()
            hasta = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=lease)) \
                .isoformat(timespec="seconds")
            previo = e["claimed_by"]
            # UN SOLO UPDATE condicional decide. Leer y luego escribir deja una
            # ventana en la que dos monitores leen "libre" y los dos creen ganar:
            # 24 de 24 hilos ganan en pruebas/prueba-reclamo-atomico.py. El motor
            # excluye; el log registra. Ver pruebas/prueba-reclamo-atomico.py.
            cur = c.execute(
                "UPDATE entries SET claimed_by=?, claim_expires=?, claim_note=?, updated=? "
                "WHERE genesis=? AND (claimed_by IS NULL OR claimed_by=? "
                "                     OR claim_expires IS NULL OR claim_expires < ?)",
                (key, hasta, b.get("nota"), ahora, g, key, ahora))
            if cur.rowcount != 1:
                actual = one(c, "SELECT claimed_by, claim_expires FROM entries WHERE genesis=?", (g,))
                c.commit()
                return self.j({"error": "ya reclamada", "por": (actual or {}).get("claimed_by"),
                               "hasta": (actual or {}).get("claim_expires")}, 409)
            # un desplazo se escribe, nunca es silencioso
            desplazo = previo if (previo and previo != key) else None
            seq = emit(c, "entrada.reclamada", genesis=g, space=e["space"], actor=key,
                       payload={"hasta": hasta, "lease": lease, "nota": b.get("nota"),
                                "desplazo_a": desplazo})
            c.execute("UPDATE monitors SET acted=acted+1, last_seen=? WHERE key=?", (now(), key))
            c.commit()
            return self.j({"ok": True, "genesis": g, "hasta": hasta, "seq": seq,
                           "desplazo_a": desplazo, "entrada": self.detail(c, g)})

        if e["claimed_by"] != key:
            return self.j({"error": "la entrada no está reclamada por este monitor",
                           "reclamada_por": e["claimed_by"]}, 409)

        if action == "progress":
            seq = emit(c, "entrada.avance", genesis=g, space=e["space"], actor=key,
                       payload={"nota": b.get("nota") or "", "datos": b.get("datos") or {}})
            renov = b.get("renovar")
            if renov:
                hasta = (dt.datetime.now(dt.timezone.utc) +
                         dt.timedelta(seconds=int(renov))).isoformat(timespec="seconds")
                c.execute("UPDATE entries SET claim_expires=? WHERE genesis=?", (hasta, g))
            c.execute("UPDATE entries SET updated=? WHERE genesis=?", (now(), g))
            c.commit()
            return self.j({"ok": True, "seq": seq})

        if action == "complete":
            st = b.get("status") or "en revision"
            if st not in STATUS:
                return self.j({"error": f"estado inválido: {st}", "validos": STATUS}, 400)
            # el arrendamiento se suelta condicionado a seguir siendo el dueño: si
            # venció y otro ya reclamó, este cierre llega tarde y no debe pisarlo
            cur = c.execute("UPDATE entries SET claimed_by=NULL, claim_expires=NULL "
                            "WHERE genesis=? AND claimed_by=?", (g, key))
            if cur.rowcount != 1:
                actual = one(c, "SELECT claimed_by FROM entries WHERE genesis=?", (g,))
                c.commit()
                return self.j({"error": "el arrendamiento ya no es tuyo; el cierre llegó tarde",
                               "reclamada_por": (actual or {}).get("claimed_by")}, 409)
            _, seq = update_entry(c, g, {"status": st}, actor=key, note=b.get("nota"))
            c.execute("UPDATE monitors SET acted=acted+1 WHERE key=?", (key,))
            c.commit()
            return self.j({"ok": True, "seq": seq, "entrada": self.detail(c, g)})

        if action == "release":
            c.execute("UPDATE entries SET claimed_by=NULL, claim_expires=NULL "
                      "WHERE genesis=? AND claimed_by=?", (g, key))
            seq = emit(c, "entrada.liberada", genesis=g, space=e["space"], actor=key,
                       payload={"motivo": b.get("motivo") or "sin motivo"})
            c.commit()
            return self.j({"ok": True, "seq": seq})
        return self.j({"error": "acción desconocida"}, 400)

    # ---- sincronización
    def drain(self, c):
        url, conn = os.environ.get("EVLOG_NEON_URL"), os.environ.get("EVLOG_NEON_CONN")
        pend = rows(c, "SELECT * FROM outbox WHERE state='pendiente' ORDER BY id LIMIT 200")
        if not (url and conn):
            return self.j({"error": "sin destino configurado",
                           "necesita": ["EVLOG_NEON_URL", "EVLOG_NEON_CONN"],
                           "pendiente": scalar(c, "SELECT COUNT(*) FROM outbox WHERE state='pendiente'"),
                           "ddl": "docs/almacen-neon.sql"}, 412)
        enviados, fallidos = 0, 0
        for row in pend:
            q = ("INSERT INTO evlog_outbox (at, entidad, clave, op, payload, digest) "
                 "VALUES ($1,$2,$3,$4,$5::jsonb,$6) ON CONFLICT (digest) DO NOTHING")
            body = jdump({"query": q, "params": [row["at"], row["entidad"], row["clave"],
                                                 row["op"], row["payload"], row["digest"]]})
            req = urllib.request.Request(url, data=body.encode(), method="POST", headers={
                "Content-Type": "application/json", "Neon-Connection-String": conn,
                "Neon-Raw-Text-Output": "true"})
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    r.read()
                c.execute("UPDATE outbox SET state='enviado', synced_at=?, attempts=attempts+1 "
                          "WHERE id=?", (now(), row["id"]))
                enviados += 1
            except Exception as ex:
                c.execute("UPDATE outbox SET state='error', attempts=attempts+1, last_error=? "
                          "WHERE id=?", (f"{type(ex).__name__}: {ex}"[:400], row["id"]))
                fallidos += 1
                break            # se detiene al primer fallo: el orden importa
        c.commit()
        return self.j({"enviados": enviados, "fallidos": fallidos, **self.sync_status(c)})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8124)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    if not os.path.exists(core.DB):
        raise SystemExit("Falta events.db — corre primero:  python3 seed.py")
    H.db = core.connect()
    srv = ThreadingHTTPServer((a.host, a.port), H)
    n = scalar(H.db, "SELECT COUNT(*) FROM entries")
    s = scalar(H.db, "SELECT COUNT(*) FROM facts")
    print(f"Event Log · {n} entradas · {s} señales · http://{a.host}:{a.port}")
    if os.environ.get("EVLOG_OPEN") == "1":
        print("  EVLOG_OPEN=1 — el bus acepta monitores SIN token (sólo para local)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nadiós")


if __name__ == "__main__":
    main()
