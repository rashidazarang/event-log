-- =====================================================================
-- Event Log · almacén en Neon Postgres
--
-- El outbox local se drena aquí en orden. `digest` es la clave de
-- idempotencia: reenviar la misma fila no duplica nada, así que un
-- reintento tras una caída es seguro.
--
--   psql "$EVLOG_NEON_CONN" -f docs/almacen-neon.sql
-- =====================================================================

CREATE TABLE IF NOT EXISTS evlog_outbox (
  id          bigserial PRIMARY KEY,
  at          timestamptz NOT NULL,
  entidad     text NOT NULL,          -- entries | events | facts
  clave       text NOT NULL,          -- génesis, seq o digest
  op          text NOT NULL,          -- insert | update
  payload     jsonb NOT NULL,
  digest      text NOT NULL UNIQUE,   -- idempotencia
  recibido_en timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_outbox_entidad ON evlog_outbox (entidad, at DESC);
CREATE INDEX IF NOT EXISTS ix_outbox_clave   ON evlog_outbox (clave);

-- Vistas materializadas sobre el flujo crudo. El outbox es el diario;
-- estas son la lectura. Se reconstruyen enteras desde el diario.

CREATE OR REPLACE VIEW evlog_entradas AS
SELECT DISTINCT ON (clave)
  clave AS genesis,
  payload->>'kind'      AS kind,
  payload->>'space'     AS space,
  payload->>'title'     AS title,
  payload->>'status'    AS status,
  payload->>'priority'  AS priority,
  payload->>'assignee'  AS assignee,
  payload->>'requester' AS requester,
  payload->'labels'     AS labels,
  payload->'meta'       AS meta,
  digest, at AS actualizado
FROM evlog_outbox
WHERE entidad = 'entries'
ORDER BY clave, at DESC, id DESC;

CREATE OR REPLACE VIEW evlog_hechos AS
SELECT
  payload->>'source'       AS fuente,
  payload->>'domain'       AS dominio,
  payload->>'action'       AS accion,
  payload->>'subject_id'   AS sujeto,
  payload->>'actor'        AS emisor,
  payload->>'target'       AS destino,
  payload->'attrs'         AS atributos,
  (payload->>'occurred_at')::timestamptz AS ocurrio,
  digest
FROM evlog_outbox
WHERE entidad = 'facts';

CREATE OR REPLACE VIEW evlog_correo_diario AS
SELECT date_trunc('day', ocurrio) AS dia,
       atributos->>'dominio'      AS dominio,
       accion,
       count(*)                   AS n
FROM evlog_hechos
WHERE dominio = 'email'
GROUP BY 1, 2, 3
ORDER BY 1 DESC, 4 DESC;
