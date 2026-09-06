# Decision Log

Registro cronológico de intercambios relevantes con el asistente. Una entrada por
intercambio: qué se intentaba, el prompt, el resumen de la respuesta y el veredicto.

Formato de cada entrada:

```
## YYYY-MM-DD HH:MM — <título corto>
<!-- id: <entry-id> -->
- **Qué intentaba:** ...
- **Prompt:** ...
- **Respuesta:** ...
- **Veredicto:** ✅ aprobado | 🔁 ajustado — ... | ❌ rechazado — ... | ⏳ pendiente
```

---

## 2026-09-04 15:45 — Configurar hook de decision log en el repo

- **Qué intentaba:** Tener un log de decisiones versionado en el repo, una entrada por intercambio relevante, con intención, prompt, resumen de respuesta y veredicto.
- **Prompt:** "Necesito que configures un hook que me permita guardar en el repo un log con los prompts y las respuestas […] un log de decisiones, una entrada por intercambio relevante, en orden cronológico. Qué intentaba (yo), el prompt, resumen de la respuesta, y mi veredicto"
- **Respuesta:** Un hook shell no puede resumir ni inferir veredicto, así que se partió en dos: `UserPromptSubmit` captura el prompt textual y me inyecta la instrucción de escribir la entrada; `Stop` bloquea el fin de turno (exit 2, con guard anti-loop) si la entrada del id pendiente no está en `docs/decision-log.md`. Config en `.claude/settings.json` del repo. 5 casos probados.
- **Veredicto:** ⏳ pendiente

## 2026-09-04 16:22 — Encuadrar el PRD del servicio de balances

<!-- id: 20260904-162243-a1fe3a40 -->
- **Qué intentaba:** Arrancar el tech challenge armando un PRD enriquecido con sus decisiones previas (ledger de doble entrada, saldo no negativo, idempotencia por clave del cliente, mono-moneda primero) y recibir challenge técnico antes de escribirlo.
- **Prompt:** "Estoy por iniciar la solución al tech challenge descrita en @docs/statement.md […] armemos un PRD enriquecido con algunas consideraciones […] Ledger contable de doble entrada […] Invariante de saldo […] Idempotencia […] Con esto dicho, arranquemos, hazme challenge o preguntame donde corresponda"
- **Respuesta:** Antes de escribir el PRD se levantaron 6 objeciones al encuadre: (1) "ninguna cuenta negativa" rompe la doble entrada — hace falta tipar cuentas y aplicar la invariante sólo a las de usuario; (2) idempotencia necesita scope + hash del payload + respuesta cacheada, no sólo la clave; (3) balance materializado con lock vs. derivado de asientos; (4) orden determinístico de lock para evitar deadlocks; (5) dinero como enteros en unidades mínimas + moneda, nunca float; (6) autorización de ownership de la cuenta origen, no sólo autenticación. Eligió transferencia atómica síncrona; se escribió `docs/prd.md`.
- **Veredicto:** 🔁 ajustado — aprobó 4.1 y 5.1; pidió aclarar el origen de la respuesta idempotente ante eviction y rechazó que FX obligue a máquina de estados

## 2026-09-04 16:37 — Corregir idempotencia durable y FX con patrón quote/execute

<!-- id: 20260904-163727-a1fe3a40 -->
- **Qué intentaba:** Cerrar dos huecos del PRD: de dónde sale la respuesta en un replay idempotente si hubiera eviction, y cuestionar que multi-moneda obligue a una máquina de estados.
- **Prompt:** "4.1 de acuerdo […] 5.1 De acuerdo, materializar en la misma transacción de posteo del asiento. 6 Me preocupa el retorno de respuesta cacheada, qué sucede en un cache eviction? de dónde nos sacamos la respuesta? 8 No creo que FX nos lleve a máquina de estados, usado el lock de BBDD no debemos tener llamadas a terceros […] quizá podamos y previo a la transación, consultar tasas de cambio, con un deadline o similar […]"
- **Respuesta:** §6 reescrito: no hay cache — el registro de idempotencia es una fila en Postgres escrita en la misma transacción que los asientos; guarda `caller_id + key + request_hash + transfer_id + status` y la respuesta se **reconstruye desde el ledger**, no se persiste serializada (evita drift de schema y duplicación). Único modo de falla real: expiración de retención, que debe ser mayor al horizonte máximo de reintentos del cliente (§6.2). §8 corregido a favor del usuario: FX se resuelve con patrón **quote/execute** — la cotización se obtiene fuera de la transacción con deadline, tiene expiry, y dentro del lock sólo se valida vigencia y se postea. Regla dura documentada: ninguna llamada a terceros bajo lock de BBDD. La máquina de estados queda reservada a settlement externo (§8.2), fuera de scope.
- **Veredicto:** ✅ aprobado

## 2026-09-04 23:07 — Reemplazar el resumen por un transcript verbatim generado

<!-- id: 20260904-230713-a1fe3a40 -->
- **Qué intentaba:** Detectar y corregir que el decision log no cumple la instrucción del statement de entregar *cada* prompt junto con *cada* respuesta — se estaba entregando sólo un resumen escrito por el modelo.
- **Prompt:** "acabo de notar que el log de prompts y respuesta parece no cumplir con lo solicitado, nos quedamos con un resumen y no es lo que solicita la instrucción \"[…] you must provide each and every prompt you used along with every response\", debemos arreglar el hook"
- **Respuesta:** Diagnóstico confirmado: el hook capturaba el prompt textual en `.decision-log-pending` y lo descartaba, la respuesta nunca se capturaba, y un prompt reenviado (21:16) se había perdido sin rastro. Se separó en dos artefactos con responsabilidades distintas: `docs/ai-transcript.md` (**generado**, verbatim, completo) reconstruido en cada corrida por `.claude/hooks/ai-transcript-build.py` desde los JSONL de sesión — determinístico, sin intervención del modelo, cubriendo todas las sesiones del proyecto; y `docs/decision-log.md` (**curado**) que queda como capa de razonamiento y veredicto. El builder se enganchó a `UserPromptSubmit`, `Stop` y `SessionEnd`; se verificó idempotencia del rebuild y se actualizaron las instrucciones del hook de decision log para que no dupliquen el transcript.
- **Veredicto:** 🔁 ajustado — preguntó si el rebuild completo pierde contenido al compactar/limpiar, lo que destapó el riesgo de retención

## 2026-09-04 23:17 — Versionar el store de turnos dentro del repo

<!-- id: 20260904-231710-a1fe3a40 -->
- **Qué intentaba:** Entender si el rebuild completo del transcript pierde contenido cuando se compacta o se limpia la sesión.
- **Prompt:** "Ese rebuild completo implica que al compactar o limpiar la sesción se pierde el contenido de los documentos?"
- **Respuesta:** Compactar y `/clear` son inocuos y se verificó con datos del repo: los prompts viven repartidos en 4 JSONL y el builder los une por timestamp. Pero la pregunta destapó un defecto real de mi diseño: los JSONL viven fuera del repo y Claude Code los purga por retención (`cleanupPeriodDays`, default 30 días); al erosionarse la fuente, un rebuild completo pisaría el transcript con uno más corto. Se introdujo `docs/.transcript-store.jsonl`, un store append-only versionado en git, keyed por uuid de turno: los session logs sólo agregan, nunca quitan. Mismo patrón que el PRD — ledger append-only + proyección materializada. Verificado reconstruyendo los 9 turnos con cero session logs disponibles.
- **Veredicto:** ✅ aprobado

## 2026-09-04 23:19 — Acotar costo de tokens y CPU de los hooks

<!-- id: 20260904-231920-a1fe3a40 -->
- **Qué intentaba:** Asegurar que los hooks de transcript y decision log no degraden la performance de la sesión ni inflen el consumo de tokens.
- **Prompt:** "Asegurate que esos hooks no nos peguen en la performance de la sesión ni el consumo de tokens"
- **Respuesta:** Medición encontró tres cosas. (1) BUG: se usaba `hash()` de Python como fallback de identidad de turno, pero su seed es aleatorio por proceso — hubiera duplicado turnos en el store indefinidamente; reemplazado por sha1 y verificado con 5 seeds distintos. (2) Tokens: el builder escribe 0 bytes a stdout (costo cero); el que costaba era `decision-log-capture.sh` con 1860 bytes (~465 tokens) por turno, recortado a 726 bytes (~181 tokens) al dejar de repetir el formato largo y de inyectar el preview del prompt. (3) CPU: reparseaba los 797 KB de todos los JSONL en cada corrida y crecía sin techo; ahora saltea archivos sin cambios vía cache de mtime+size (`.claude/.transcript-scan-cache.json`, gitignorado) y prefiltra líneas antes del `json.loads` — trabajo real de 14 ms a 5-6 ms, dominado por los 42 ms de arranque del intérprete.
- **Veredicto:** ✅ aprobado

<!-- skip: 20260904-232059-abcd1234 -->

## 2026-09-04 23:26 — Definir comando de commits referenciado al PRD

<!-- id: 20260904-232627-a1fe3a40 -->
- **Qué intentaba:** Tener un comando del repo para hacer commits con Conventional Commits que, al no haber Jira, referencien el PRD.
- **Prompt:** "quiero que definas un comando o skill del repositorio para realizar los commits, quiero conventional commits, en este caso no usaremos jira por lo que los commits deben referenciar al PRD"
- **Respuesta:** Creado `.claude/commands/commit.md`, que pisa al `commit.md` global (basado en Jira y en `poetry run pre-commit`). Decisión clave: el trailer `PRD:` prioriza los **IDs estables** `G1`-`G5` e `I1`-`I7` por sobre los números de sección, porque las secciones se renumeran al insertar nuevas — ya pasó esta sesión con §6.1, §6.2, §8.1 y §8.2. Se exige `n/a — <razón>` cuando el cambio no toca el contrato de producto, para que sea afirmación deliberada y no omisión. Se agregó regla de separar en su propio commit los artefactos generados (`ai-transcript.md`, `.transcript-store.jsonl`, `decision-log.md`) que cambian cada turno y ensuciarían todo diff de feature. Scopes derivados de la estructura hexagonal real del repo. Verificados contra el PRD los IDs y secciones citados en los ejemplos.
- **Veredicto:** ✅ aprobado

## 2026-09-04 23:29 — Commitear el estado inicial del repositorio

<!-- id: 20260904-232916-a1fe3a40 -->
- **Qué intentaba:** Versionar todo lo construido hasta ahora, que estaba sin commitear (el repo no tenía ningún commit).
- **Prompt:** "procede a committear lo existente"
- **Respuesta:** Cinco commits atómicos siguiendo `.claude/commands/commit.md`: scaffolding hexagonal, statement + PRD, hooks de transcript/decision-log, comando de commit, y por último los artefactos de sesión generados. Gate de pre-commit salteado por condición (no existe `.pre-commit-config.yaml` todavía). Escaneo de secretos previo: único hit fue la propia regla del comando que prohíbe commitear secretos. Se agregó `.atl/.skill-registry.cache.json` al gitignore por ser derivado. **Hallazgo:** el `Skill` tool cargó el `commit.md` GLOBAL (Jira + poetry) en vez del `.claude/commands/commit.md` del repo — el archivo del repo no tomó precedencia; se commiteó siguiendo la convención del repo igual, pero la resolución del comando hay que corregirla.
- **Veredicto:** ✅ aprobado

## 2026-09-04 23:31 — Configurar proyecto Python 3.14 con uv y gates de calidad

<!-- id: 20260904-233101-a1fe3a40 -->
- **Qué intentaba:** Dejar el proyecto Python configurado con la versión más nueva, virtualenv y pre-commit.
- **Prompt:** "configura el proyecto python, usa la versión mas actualizada, agrega el virtual env y por último precommit"
- **Respuesta:** Elegido **uv** sobre poetry (fork consultado; contradecía su convención global `poetry run`): gestiona intérprete + venv + dependencias en un binario y fija Python en el proyecto, lo que da reproducibilidad entre local, contenedor y CI — relevante porque el statement pide pensar el despliegue cloud-native. Se verificó **empíricamente** que el stack (FastAPI, SQLAlchemy, Alembic, psycopg, testcontainers) resuelve en 3.14 antes de comprometerse. Gates: ruff lint+format, mypy strict, y un validador commit-msg propio (`scripts/check_commit_msg.py`, 8 casos probados) que hace cumplir el trailer `PRD:` — la convención deja de ser documento y pasa a ser gate.
- **Hallazgos:** (1) El autofix de ruff reescribió `except (A, B):` sin paréntesis usando PEP 758 de Python 3.14; **no era un bug** —lo verifiqué antes de afirmarlo— pero los hooks corren con el `python3` del SISTEMA, no el venv, así que rompían en 3.12; se excluyó `.claude/hooks` de ruff y se restauró la forma portable. (2) Se descartó `no-commit-to-branch` porque bloquearía el flujo real del repo y un gate que se saltea rutinariamente con `--no-verify` enseña a saltear gates. (3) `T201` frenó el commit por los `print` del validador: se resolvió con per-file-ignore acotado a `scripts/**` en vez de aflojar la regla global.
- **Veredicto:** ✅ aprobado

## 2026-09-04 23:47 — Instalar el proyecto editable para resolver imports de src

<!-- id: 20260904-234714-a1fe3a40 -->
- **Qué intentaba:** Entender por qué `from modules...` no resuelve tras agregar un servicio generador de IDs y dependency-injector.
- **Prompt:** "agregué un generador de ids como servicio, agregué dependency injector y veo un reto con los imports, deberían incluir src pero no lo hace, parece falta consigurar algo, qué es?"
- **Respuesta:** Causa raíz en **mi** setup, dos partes: (1) `[tool.uv] package = false` dejaba el proyecto fuera del venv, así que nada ponía `src` en `sys.path`; (2) `pythonpath = ["src"]` vivía en `[tool.pytest.ini_options]`, que **sólo lee pytest** — tapaba el síntoma bajo tests mientras `uv run python`, uvicorn y el editor seguían fallando. Reemplazado por instalación **editable** vía hatchling (`build-system` + `[tool.hatch.build.targets.wheel] packages = ["src/modules"]`) y eliminado el `pythonpath`, para que tests y servicio resuelvan imports por el mismo mecanismo. Los `.gitkeep` se reemplazaron por `__init__.py` (34 paquetes explícitos): mantienen el directorio y evitan namespace packages implícitos, que mypy strict maneja mal. Verificado: `modules` apunta a `src/modules` real, el contenedor DI instancia el singleton, y los 11 gates pasan incluido mypy strict sobre el código nuevo.
- **Veredicto:** ✅ aprobado

## 2026-09-04 23:52 — Unificar la generación de IDs en UUIDv7

<!-- id: 20260904-235211-a1fe3a40 -->
- **Qué intentaba:** Adoptar UUIDv7 para todos los IDs y aprovechar su ordenamiento, eliminando la variante aleatoria.
- **Prompt:** "vale, usemos uuid7 y su bondad de ordenamiento para todo"
- **Respuesta:** `IdGenerator` queda con un único método `next_id()`. Se eliminó el par `sorted_uuid`/`unsorted_uuid`: nombraba el mecanismo en vez de la intención y obligaba a decidir en cada call site si la entidad merecía un ID ordenado — basta que esa decisión se tome mal una vez, en una tabla que nadie mira, para fragmentar su índice. UUIDv7 lleva timestamp adelante, así los inserts caen cerca del borde derecho del B-tree en lugar de dispersarse; es lo que necesitan las tablas append-only del ledger y no cuesta nada en el resto. Primeros tests del proyecto (`tests/unit/shared/test_id_generator.py`) cubriendo las tres propiedades en que se apoya la decisión: versión, unicidad y orden creciente. Valida además la cadena completa editable install + pytest.
- **Tradeoff registrado:** UUIDv7 expone el instante de creación dentro del ID. Aceptable acá porque el ledger ya expone timestamps al dueño de la cuenta, pero conviene tenerlo presente si algún ID llega a superficies públicas.
- **Veredicto:** ✅ aprobado

## 2026-09-04 23:54 — Alinear el editor con el venv y los gates del proyecto

<!-- id: 20260904-235428-a1fe3a40 -->
- **Qué intentaba:** Entender por qué el IDE marca error de pylint en los imports de los tests si en runtime y pytest resuelven bien.
- **Prompt:** "aunque funciona, del lado de los tests, el IDE me marca error de pylint en el import, algo por ajustar?"
- **Respuesta:** Causa raíz: el editable install expone `src` mediante un `.pth` dentro de `.venv/lib/python3.14/site-packages/`, y el pylint que marca el error viene empaquetado con el editor y corre sobre **otro intérprete** que nunca lee ese `.pth`. Verificado que pylint no está ni en el venv, ni en PATH, ni en `pyproject.toml`, ni en pre-commit. Se agregó `.vscode/settings.json` y `.vscode/extensions.json` versionados: intérprete apuntando a `.venv`, pylint/flake8/black deshabilitados, y ruff + mypy resueltos `fromEnvironment` para que el editor use las versiones fijadas en `uv.lock`.
- **Razonamiento de fondo:** el problema mayor no era el subrayado sino la desalineación — un editor que aplica un linter que el repo nunca adoptó marca lo que a CI no le importa y calla lo que sí bloquea el commit, y eso entrena a ignorar diagnósticos. Ahora lo que ve el editor es exactamente lo que valida el gate.
- **Veredicto:** ✅ aprobado

## 2026-09-05 00:01 — Implementar Money y arrancar SDD del dominio

<!-- id: 20260905-000131-a1fe3a40 -->
- **Qué intentaba:** Empezar el dominio por `Money` en shared, y encarar el dominio general vía SDD arrancando por la proposal para pulirla él.
- **Prompt:** "dale, arrancá con Money en shared. Para el dominio en general, me parece lo core del asunto, iniciaremos con sdd vamos con la proposal y yo lo pulo […]"
- **Respuesta:** `Money` + `Currency` como value objects frozen con slots, y `errors.py` con `DomainError` de base. Decisiones: enteros en unidades mínimas sin constructor que acepte float; `bool` rechazado explícitamente porque es subclase de `int` y pasaría silenciosamente como 1; moneda como parte del valor, no metadata al lado; **ordenamiento escrito a mano** en vez de `order=True` porque el generado compara la tupla `(amount, currency)` y respondería "100 JPY < 5 USD" en lugar de rechazar la pregunta; igualdad **total** a propósito (no lanza) porque la usan contenedores y asserts; `Currency` sin exponente de unidades mínimas, ya que dentro del servicio el dinero sólo existe en unidades mínimas y el exponente es presentación, propia del adapter de entrada. 35 tests.
- **Hallazgos:** (1) N818 exigió sufijo `Error` en las excepciones; se renombró en vez de silenciar la regla, porque los adapters que las atrapan viven fuera del dominio y esperan la convención de Python — y se actualizó el PRD (I2) para que no derivara del código. (2) **`pre-commit run --all-files` sólo revisa archivos trackeados por git**: pasó en verde con los archivos nuevos untracked y falló recién al hacer `git add`.
- **SDD:** sin `sdd-init` previo, se registró el contexto del proyecto en engram (`sdd-init/supercool-finances-account-balance`) con stack, capacidades de testing y convenciones. Artefactos en modo **openspec** (archivos versionados) porque él los va a pulir. Proposal del dominio delegada a sub-agente.
- **Veredicto:** ⏳ pendiente

## 2026-09-05 00:04 — Quitar el import de future annotations por redundante en 3.14

<!-- id: 20260905-000404-6ab996d1 -->
- **Qué intentaba:** Entender para qué servía la línea `from __future__ import annotations` en el código nuevo. (Pregunta hecha en otra sesión del mismo repo; el hook de decision log es del repo, así que el pendiente llegó a esta.)
- **Prompt:** "para que sirve l línea from __future__ import annotations"
- **Respuesta:** Estaba para que `def __add__(self, other: Money) -> Money` pudiera nombrar la clase desde adentro de su propio cuerpo, donde el nombre todavía no está ligado. Verificado empíricamente en ambos intérpretes: en 3.12 sin el import es `NameError`; en 3.14 funciona solo, porque PEP 649 hace nativa la evaluación diferida. Además se comprobó que **mantenerlo es peor que sacarlo**: el import del futuro aplica PEP 563 y deja las anotaciones como strings (`{'other': 'Box'}`), mientras que 3.14 sin él las deja como objetos reales resueltos on demand (`{'other': <class 'Box'>}`). Todo lo que introspecciona tipos en runtime —el wiring de `dependency-injector`, pydantic— tendría que resolverlas a mano. Eliminado de `money.py` y de `scripts/check_commit_msg.py`.
- **Veredicto:** ✅ aprobado

## 2026-09-05 00:10 — Revisar la proposal del dominio y detectar hueco de autorización

<!-- id: 20260905-001056-a1fe3a40 -->
- **Qué intentaba:** (Turno disparado por la finalización del sub-agente de SDD, no por un prompt del usuario.) Entregar la proposal del dominio revisada.
- **Prompt:** — (notificación de tarea en background)
- **Respuesta:** Proposal en `openspec/changes/account-balance-domain/proposal.md` (354 líneas): `Account` / `Transfer` / `Entry`, 12 decisiones con su alternativa rechazada, mapa de enforcement de I1–I7 y taxonomía de errores con clasificación Client/Internal. **Hallazgo confirmado contra el PRD:** §5 paso 1 exige que el llamador sea dueño de la cuenta *origen*, pero el origen de un depósito es una cuenta `SYSTEM` que nadie posee (§7) — la regla bloquea todo depósito. Es un hueco del PRD, no de la proposal.
- **Corrección a la proposal:** su restitución de G5 —"el llamador debe ser dueño de **toda** pata `USER` de la transferencia"— **rompe P2P a terceros**: el destino de un envío a otra persona es una cuenta `USER` ajena, así que la regla exigiría poseer la cuenta del destinatario. La formulación correcta gira sobre la pata *debitada*, no sobre todas. Confirmado que P2P a terceros entra en v1, se corrigió el PRD con un nuevo §9.1 que enuncia G5 sobre la pata debitada y una tabla que resuelve los cuatro movimientos sin excepciones; la proposal quedó alineada y con Q1 resuelta.
- **Veredicto:** ✅ aprobado — P2P a terceros dentro de alcance

## 2026-09-05 08:32 — Cerrar las preguntas abiertas del dominio

<!-- id: 20260905-083256-a1fe3a40 -->
- **Qué intentaba:** Resolver Q2–Q5 de la proposal y entender las dos restricciones futuras que quedaban señaladas.
- **Prompt:** "Q2 - es correcto, close como funcionalidad de dominio, invariante de saldo cero […] Q3 - La reversión viene dada por un asiento compensatorio, resuelve la duda? Q4 - Un operador […] Q5 - Si. Ahondemos sobre esas restricciones futuras que mencionas, no estoy seguro de haber entendido"
- **Respuesta:** **Q2** cierre como comportamiento de dominio (`Account.close()`, sólo `USER`, exige saldo cero; la cuenta cerrada sigue siendo legible) → PRD §7.2. **Q4** reversa sólo por operador: habilitar al pagador convierte todo pago completado en uno que puede retirar unilateralmente, indistinguible de un robo del lado que recibe → PRD §7.1. **Q5** módulo `account_balance`.
- **Q3 — el asiento compensatorio NO resolvía la duda:** son dos preguntas distintas. I7 dice *cómo* revertir; Q3 pregunta qué hacer cuando ese asiento **no se puede postear** porque el destinatario ya gastó el dinero y debitarlo violaría I2. Decidido: postear lo recuperable y absorber el faltante en una cuenta `SYSTEM` de incobrables (Bruno 20 + receivable 80 → Ana 100). El ledger cierra, I2 se mantiene sin excepción, el pagador queda entero y la deuda queda declarada; el saldo de esa cuenta es el acumulado que el negocio se comió. Rechazadas: rechazar la reversa (el reclamante queda sin dinero y sin registro contable) y permitir romper I2 con autorización de operador (una invariante con excepción deja de serlo, y presta dinero sin decidirlo como producto). **Consecuencia:** una transferencia no siempre tiene dos patas — esto confirma D4, que enuncia I1 como neteo por moneda en vez de "un débito y un crédito", formulación que no podría expresar esta reversa. → PRD §7.3.
- **Restricciones explicadas:** (1) *doble reversa*: un agregado sólo garantiza invariantes sobre su propio estado, y "¿ya fue revertida?" es una pregunta sobre la existencia de **otra** `Transfer`; el chequeo en el caso de uso no basta porque dos reversas concurrentes leen "no hay" a la vez y ambas postean — la garantía real es un índice único parcial sobre `transfers.reverses`. (2) *dos agregados por transacción*: se viola a propósito la guía un-agregado-por-transacción, porque aplicarla significaría debitar ahora y acreditar después, dejando el dinero sin existir en ningún lado entre ambos momentos — exactamente lo que el posteo atómico de §5 existe para impedir.
- **Veredicto:** ✅ aprobado

## 2026-09-06 11:26 — Revisar el PRD: locking, saldo negativo, idempotencia y observabilidad

<!-- id: 20260906-112610-a1fe3a40 -->
- **Qué intentaba:** Corregir seis puntos del PRD tras releerlo: locking de cuentas SYSTEM, G4 y saldos negativos, alcance de la idempotencia, campo status muerto, una cuenta por moneda, y ausencia de observabilidad.
- **Prompt:** "Indicamos locking sobre ambas cuentas en una transacción, esto no debe aplicar en depósitos […] G4, creo si debemos soportar que una cuenta se vaya a negativo, pero solo a través de una reversión […] Sobre la idenpotencia, parece algo a mantener en los movimientos de dinero […] el status no me está haciendo sentido […] un usuario podría tener más de una cuenta con una misma moneda […] No tenemos nada en observabilidad, y nos lo piden"
- **Respuesta:** Cinco correcciones aplicadas al PRD. (1) **§5.3 nueva**: las cuentas `SYSTEM` nunca se bloquean ni materializan saldo. Se bloquea para que un saldo no cambie bajo una regla que va a leerlo, y la única regla es I2, que no aplica a `SYSTEM`; bloquear la cuenta de fondeo serializaría **todos** los depósitos del sistema por una sola fila. Un depósito ahora bloquea exactamente una fila. (2) **G4/I2 reformuladas**: un saldo `USER` puede ir a negativo pero **sólo por reversión**; I2 sigue siendo total porque se nombra el camino en vez de agregar un flag — `debit_for_reversal()` es el único método que puede cruzar cero, y un grep de ese nombre audita todos los lugares donde un saldo puede ponerse negativo. Reemplaza la cuenta `SYSTEM` de incobrables decidida el turno anterior: la deuda vuelve a la cuenta que la debe, es atribuible y se salda sola con el próximo depósito. (3) **§6.3**: llaves de idempotencia sólo para movimientos de dinero, más apertura de cuenta; el cierre valida su propia precondición (`ACTIVE`). (4) **§4.2**: un titular puede tener varias cuentas en la misma moneda. (5) **§11 nueva, observabilidad**: señales de corrección con alerta al primer evento (desbalance del ledger, drift de saldo), de comportamiento (tasa de `InsufficientFunds`, colisiones vs. replays de idempotencia, espera en lock) y de riesgo (total y antigüedad de saldos negativos como exposición crediticia).
- **Corrección técnica al usuario:** afirmó que "la reversión viola la invariante I1 a nivel de cuentas de usuario". I1 es sobre la transferencia completa y **se cumplía**: en la reversa de tres patas los débitos sumaban 100 (20+80) y los créditos 100. Lo que observó es real pero no es una violación — mirando *sólo* cuentas `USER` los montos no cuadran, que es exactamente cómo funciona la partida doble con cuentas de sistema; un depósito tiene la misma propiedad (crédito a `USER` sin débito `USER`). El campo `status` en `Transfer` ya había sido eliminado por D9; el `status` que queda es el de `Account`, necesario para el cierre que él mismo decidió en Q2.
- **Veredicto:** 🔁 ajustado — pidió eliminar también la llave de idempotencia en la apertura, usando tipos de cuenta como precondición

## 2026-09-06 11:47 — Introducir propósito de cuenta y quitar la llave de idempotencia en apertura

<!-- id: 20260906-114700-a1fe3a40 -->
- **Qué intentaba:** Eliminar la última excepción del esquema de idempotencia: que la apertura de cuenta necesitara llave. Propone soportar tipos de cuenta, uno por tipo por usuario, y que esa sea la precondición.
- **Prompt:** "NO quiero la llave de idempotencia en la apertura o creación, prefiero un aproach donde soportamos tipos de cuenta, y hasta una cuenta por tipo por usuario, allí la precondición, idempotencia para movimientos de dinero. Hagamonos la vida simple"
- **Respuesta:** Nuevo **§4.4**: `AccountPurpose` como eje **independiente** de `AccountType` — se señaló el choque de nombres, porque `type` (`USER`/`SYSTEM`) es la clasificación contable que define la política de sobregiro, mientras que el propósito dice para qué sirve la cuenta. `USER` toma `CHECKING`/`SAVINGS`, `SYSTEM` toma `FUNDING`/`SETTLEMENT`; cada propósito pertenece a un solo tipo y el par se valida al abrir, así que una combinación inválida no se puede construir. El propósito aplica también a las cuentas de sistema, con lo cual la unicidad `(titular, propósito, moneda)` queda pareja para ambas. **Esa clave natural reemplaza un mecanismo**: la apertura deja de llevar llave de idempotencia porque el reintento se responde desde los datos. Se enfatizó que debe enforcarse con constraint único y no con check-then-insert, porque dos aperturas simultáneas leerían "no existe" y ambas insertarían — la misma carrera que obliga al índice en la doble reversa. **§6.3 reescrito**: la condición que justifica una llave es precisa —aplicar dos veces difiere de aplicar una, y nada en los datos resultantes las distingue— y sólo los movimientos de dinero la cumplen; la seguridad que viene de los datos es superior porque **no expira**. Rechazada la alternativa de etiqueta libre de cuenta: más flexible y destruye la unicidad que hace segura la reapertura.
- **También:** se corrigió el orden de §5.2/§5.3 y §6.2/§6.3, que habían quedado invertidos al insertar secciones.
- **Veredicto:** ✅ aprobado

## 2026-09-06 11:50 — Alinear la proposal y escribir el README de presentación

<!-- id: 20260906-115035-a1fe3a40 -->
- **Qué intentaba:** Bajar las revisiones del PRD al cuerpo de la proposal, y crear el README como carta de presentación de la solución, releyendo el statement.
- **Prompt:** "1. Si no lo has hecho, ajusta la proposal de dominio con estas nuevas consideraciones del PRD. 2. Arma el readme en base al PRD y el conocimiento que has ganado en esta sesión […] debe ser la carta de presentación de la solución, con decisiones, tech y no tech, lo rechazado y sus motivos"
- **Respuesta:** **Proposal:** el changelog §8b ya registraba los deltas, pero el cuerpo seguía describiendo el modelo superado y un revisor que lee de arriba hacia abajo chocaba primero con lo viejo. Se enmendó D1 (la política de sobregiro ahora se llavea por operación además de por tipo de cuenta, expresada como **dos métodos nombrados** en vez de un flag `allow_overdraft=True`: un booleano se puede pasar desde cualquier lado, un segundo método hay que nombrarlo, y así `grep debit_for_reversal` es la auditoría completa), se agregó `AccountPurpose` a la tabla de tipos, se actualizó el mapa de I2, y se juntaron las dos reglas que ningún agregado puede enforcar con sus constraints (índice parcial en `transfers.reverses`, único en `(owner, purpose, currency)`).
- **README:** escrito como entrada a la solución, en inglés como el resto de artefactos. Decisión de fondo: **encabezar con una sección de estado honesta** que separa lo construido (Money/Currency con 35 tests, generación de IDs, toolchain con gates) de lo diseñado pero no escrito (Account/Transfer/Entry, casos de uso, API, persistencia, contenedores, IaC). Un README que exagera su objeto es lo primero que un revisor deja de creer, y el statement premia criterio sobre volumen. Incluye defensa de la arquitectura con sus alternativas rechazadas (monolito modular, servicios ledger/balance separados), tabla de decisiones técnicas con lo que cada una derrotó, tabla de decisiones **no técnicas** (por qué fraude, outbox, auth real y multi-moneda quedan fuera), observabilidad, plan de despliegue cloud e IaC marcado como pendiente, y la sección de uso de IA explicando por qué el transcript es generado y no resumido. Se citó como ejemplo verificable el caso en que el sub-agente propuso una regla de autorización rota. Links internos y afirmaciones verificados antes de commitear.
- **Veredicto:** ⏳ pendiente
