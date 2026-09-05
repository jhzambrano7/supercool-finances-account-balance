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
- **Veredicto:** ⏳ pendiente
