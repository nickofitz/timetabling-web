# Horarios universitarios: aplicación web

Sube los datos de una facultad (salas, docentes, secciones, disponibilidades), elige un
solver y obtén un horario sin choques, verificado por un auditor independiente.

Es la capa web de dos proyectos anteriores, que usa sin modificarlos:

- [academic-timetabling-demo](https://github.com/nickofitz/academic-timetabling-demo): formato
  de datos, validación, prefiltro SQL, modelo CP-SAT, auditor y grilla HTML.
- [timetabling-milp-comparison](https://github.com/nickofitz/timetabling-milp-comparison): el
  mismo modelo en Pyomo con HiGHS, la restricción de compacidad y el generador de instancias.

## Qué hace

1. Recibe un ZIP con 20 CSV y `config.json`, o uno de cinco ejemplos (3 a 200 secciones).
2. Valida los datos e informa archivo, fila y campo de cada problema.
3. Descarta por SQL las combinaciones (sección, patrón, sala) que violan una regla dura. Si una
   sección se queda sin alternativas, se detiene y explica por qué, sin llamar al solver.
4. Resuelve con HiGHS (programación lineal entera) o CP-SAT (programación por restricciones),
   con un límite de tiempo y, si se pide, una penalización por ventanas.
5. Audita el horario sin usar el solver y lo muestra en una grilla filtrable por sala, docente
   y grupo, descargable en CSV y HTML.

El estado se informa sin exagerar: `OPTIMAL` solo cuando el solver lo demostró; si no, el
mejor horario encontrado con su brecha respecto de la cota inferior.

## Arquitectura

```
navegador ──▶ Vercel: función Python (FastAPI + plantillas Jinja)
                 ├─ inputs.py    ZIP o ejemplo ──▶ Instance validada
                 ├─ pipeline.py  SQLite en memoria ▸ prefiltro ▸ solver ▸ auditoría ▸ grilla
                 └─ store.py     opcional ──▶ Supabase: tabla runs (Data API) y bucket instances
```

Sin estado en el servidor: cada petición resuelve y responde. Supabase solo guarda el
historial; sin sus variables de entorno, la aplicación funciona igual y no guarda nada.

## Uso local

Requiere [uv](https://docs.astral.sh/uv/).

```
uv sync
uv run pytest
uv run uvicorn app:app --reload
```

## Despliegue

**Vercel.** Importar este repositorio en Vercel; detecta FastAPI y `app.py` sin configuración.
Variables de entorno, todas opcionales:

| Variable | Para qué |
|---|---|
| `DEMO_ACCESS_CODE` | Si se define, el formulario pide este código antes de resolver. Recomendado: la URL es pública y cada corrida consume cómputo. |
| `SUPABASE_URL` | URL del proyecto, `https://<ref>.supabase.co`. |
| `SUPABASE_SECRET_KEY` | Clave secreta `sb_secret_...` (Settings ▸ API Keys). Solo en el servidor; nunca en el navegador. |

**Supabase**, para el historial. Crear un proyecto, ejecutar
`supabase/migrations/0001_runs.sql` en el SQL Editor y cargar las dos variables en Vercel. La
tabla tiene RLS activado sin políticas y un GRANT solo para `service_role`: las claves públicas
no ven nada.

## Límites

| Límite | Valor | Motivo |
|---|---|---|
| Tamaño del ZIP | 2 MB (20 MB descomprimido) | Evitar abusos; el ejemplo de 200 secciones pesa 18 KB |
| Secciones | 300 | Mantener la resolución dentro de la duración de una función |
| Límite de tiempo del solver | 60 s | En el plan Hobby una función de Vercel corre hasta 300 s |
| Solvers | HiGHS y CP-SAT | CBC ocupa unos 550 MB y no cabe en una función (límite de 500 MB) |

Dos ajustes al entorno de Vercel, en `timetabling_web/_serverless.py`, que solo se activan si
hacen falta:

- **SQLite.** El prefiltro del original exige SQLite 3.44 o posterior y el Python de Vercel trae
  uno anterior. Se usa el de `pysqlite3-binary` (3.51) cuando el del sistema no basta.
- **Memoria compartida.** Las funciones corren sobre AWS Lambda, sin `/dev/shm`, y Pyomo no
  tolera ese error al cargarse. Se sustituye el lock de procesos por uno de hilos; la app no
  lanza subprocesos.

Otras advertencias:

- **Arranque en frío.** La primera petición tras un rato sin uso tarda unos segundos más
  mientras carga OR-Tools y Pyomo.
- **Supabase pausa los proyectos gratuitos** tras una semana sin actividad; antes de una
  demostración conviene abrir el panel y reactivarlo.
- **CPU.** CP-SAT reparte la búsqueda entre varios workers y rinde menos con pocos núcleos. En
  las pruebas con un núcleo, HiGHS llegó más lejos que CP-SAT en las instancias ajustadas; con
  ocho workers, CP-SAT lo igualó en `medium`. Detalle en la comparación.
- **Datos sintéticos.** Los ejemplos vienen de generadores; no representan una universidad real.

## Estructura

```
app.py                    punto de entrada para Vercel
timetabling_web/
  inputs.py               lectura segura del ZIP y ejemplos descargables
  pipeline.py             orquesta prefiltro, solver, auditoría y grilla
  store.py                historial opcional en Supabase (Data API y Storage)
  main.py                 rutas HTTP
  templates/              páginas
supabase/migrations/      esquema de la tabla runs
tests/                    pruebas (sin red: Supabase se simula)
```
