# Por qué está construido así

Lo que sigue no es una lista de buenas intenciones. Cada regla está aquí porque
**cazó un defecto concreto durante la construcción de este sistema**, y cada una
dice cuál. Una práctica sin su cicatriz se olvida en dos semanas.

---

## 1. El motor excluye; el registro anota

Un registro append-only no puede servir de candado. Bajo repetición, dos
reclamos simultáneos "ganan" los dos: cada uno lee "libre" antes de que el otro
escriba.

**Lo que encontró.** El reclamo era un `SELECT` y luego un `UPDATE`, salvado sólo
por un candado de proceso. `pruebas/prueba-reclamo-atomico.py` lanza 24 hilos
contra la misma entrada: **los 24 creyeron ganar**. Con un solo `UPDATE`
condicional gana exactamente uno, y da igual cuántos procesos sirvan la base.

**El corolario que casi se escapa:** lo mismo aplica al cierre. Si el
arrendamiento venció y otro suscriptor ya reclamó, el cierre tardío del primero
pisaba el trabajo del segundo. `complete` condiciona el soltado a seguir siendo
el dueño y responde `409` cuando llega tarde.

Y dos reglas que vienen con el patrón: un desplazo por caducidad **se escribe**
en el reclamo nuevo, nunca es silencioso; y sin identidad determinable **se dice
en voz alta** en vez de fingir orden.

## 2. Una corrida fallida nunca avanza la línea base

**Lo que encontró.** El monitor reactivo confirmaba el lote entero al terminar la
vuelta, aunque las acciones hubieran fallado. El servidor vivo todavía no tenía
un endpoint que el monitor usaba, las nueve asignaciones dieron `404`, y **el
cursor avanzó igual**: nueve eventos dados por vistos sin haberse atendido,
perdidos en silencio.

El suscriptor de referencia en bash ya lo hacía bien —el cursor no avanza si el
manejador falla— y el de Python no. Ahora sólo se confirma el **prefijo
atendido**, y un `409` (otro lo tiene) se distingue de un `500` (infraestructura
rota): el primero es un desenlace legítimo, el segundo se reintenta.

## 3. El instrumento se nombra por contenido

El bucle proactivo publica su versión y el sha256 de su propio archivo en cada
lectura. Dos lecturas con el mismo número y distinto digest no son la misma
medición.

Es la misma lección que la cadena de identidad del sistema: cuando un eslabón se
nombra por etiqueta en vez de por contenido, la cadena parece intacta y no lo
está.

## 4. Un umbral no se mueve para que algo pase

**Lo que encontró.** La primera corrida falsificó una predicción: se esperaba una
cola por encima de su piso y la lectura dio menos de un cuarto. La salida fácil
era bajar el piso y declarar el hallazgo. Lo correcto era lo contrario: **la
predicción estaba mal** —confundía "la cola crece" con "la cola cruzó el piso"—
y el umbral se quedó donde estaba. La falsificación quedó escrita en el ledger.

Un umbral se cambia subiendo la versión del instrumento y publicando su tabla de
deriva, nunca para que una lectura incómoda pase.

## 5. Una prueba que pasa porque su objeto desapareció no asserta nada

**Lo que encontró.** La sonda de estancamiento medía contra la fecha de última
actualización. Pero esa fecha la levanta cualquiera —incluido el monitor
reactivo al comentar—, así que en cuanto los dos agentes convivieron **la sonda
no podía dispararse jamás**: daba cero y parecía salud.

Ahora mide el último evento de un actor humano. El arreglo subió la versión del
instrumento, y la corrida siguiente publicó su tabla de deriva: se movió esa
sonda y **ninguna otra**. Los movedores deben ser exactamente los que el arreglo
predice; si se mueve otro, el instrumento movió más de lo que decía.

## 6. Falta de acceso no es cero

Una sonda que no pudo medir anota `null` y marca la corrida como no comparable.
Con `0`, un servidor caído se lee como salud perfecta y el ledger acumula ceros
tranquilizadores. El resumen saca las lecturas `null` a su propia sección para
que nadie las promedie con los números reales.

## 7. La predicción se escribe antes de la medición que la prueba

Las predicciones se generan **antes** que las sondas y se guardan en la misma
fila. No se pueden ajustar después: ya están escritas. Una predicción falsificada
es un resultado.

## 8. Append-only, y una corrección es una fila nueva

Dos bitácoras, las dos append-only y con `fsync` por línea: una fila por corrida
del bucle, una línea por decisión del monitor. Nada se reescribe. La corrección
del instrumento del punto 5 no borró la lectura vieja: publicó la deriva junto a
ella.

## 9. El bucket de descartes se abre siempre

El monitor reactivo anota **lo que decidió no hacer**, con su razón: en la
primera corrida, 141 descartes contra 9 asignaciones. Un triage que sólo
registra sus aciertos no se puede auditar, porque la pregunta interesante nunca
es qué atendió: es qué dejó pasar.

## 10. Copiar un mecanismo sin su razón acumula ceremonia

Un arnés de medición que compite por CPU necesita un candado de máquina antes de
medir una fila. Este sistema no compite así, y por eso **no** lo tiene. Lo que sí
se heredó es el campo de entorno en cada fila —totales al momento de medir—,
porque una lectura sin las condiciones en las que se tomó no se compara con
nada.
