# Notebookvibes — análisis de integraciones

Fecha: 2026-09-17

## Ajuste con el perfil

Notebookvibes está orientado a investigación profunda, contraste de fuentes, síntesis con trazabilidad y propuesta de material para Open Notebook. La integración aporta valor cuando mejora al menos una de estas fases:

1. descubrir evidencia;
2. recuperar contenido primario;
3. reconstruir contexto y decisiones;
4. organizar seguimiento;
5. conservar conocimiento reutilizable.

La Mini App solo vincula cuentas y consulta su estado. No lee ni modifica datos por sí misma. La ejecución ocurre en el servidor y debe respetar la intención y permisos del usuario.

## Priorización

| Prioridad | Integración | Valor para Notebookvibes | Uso recomendado |
|---|---|---|---|
| 1 | Google Drive | Alto | Buscar PDFs, documentos y fuentes primarias guardadas en Drive. |
| 2 | Notion | Alto | Consultar wikis, páginas y bases internas compartidas. |
| 3 | YouTube | Alto | Descubrir videos y canales como material de investigación; validar antes de guardar. |
| 4 | GitHub | Alto para investigación técnica | Leer documentación, repositorios, issues y cambios con referencias estables. |
| 5 | Gmail | Medio/alto | Recuperar contexto, acuerdos y adjuntos de conversaciones autorizadas. |
| 6 | Google Calendar | Medio | Relacionar reuniones, fechas y seguimientos con investigaciones y decisiones. |
| 7 | Slack | Medio/alto en equipos | Encontrar decisiones, hilos y contexto que no llegó a documentación formal. |
| 8 | Dropbox | Medio | Recuperar archivos y fuentes fuera del ecosistema de Google. |

## Evidencia técnica verificada

La búsqueda real de herramientas de Composio confirmó estos toolkits y capacidades:

- `googledrive`: búsqueda, metadatos, descarga y exportación de archivos.
- `slack`: búsqueda de mensajes, canales, hilos y enlaces permanentes.
- `github`: repositorios, contenido, árboles, issues, commits y pull requests.
- `youtube`: búsqueda de videos, detalles, canales y playlists.
- `dropbox`: búsqueda, listado y lectura de archivos.

Las integraciones recomendadas siguen destacadas, mientras que la vista `Conectadas` refleja todas las cuentas activas detectadas en el catálogo vivo.

## Decisión de producto

Se mantiene una entrada simple con ocho recomendaciones, pero ahora también se expone el catálogo vivo de Composio mediante las vistas `Recomendadas`, `Conectadas` y `Todas`. La consulta actual devuelve 1.000 integraciones —el máximo expuesto por la CLI en una sola respuesta— y el buscador filtra por nombre, descripción, slug y capacidad. Para proteger el rendimiento móvil, la interfaz renderiza 24 tarjetas por bloque mediante `Mostrar más`. El backend solo permite vincular slugs presentes en el catálogo validado y trata las integraciones sin autenticación como disponibles sin OAuth.

## Acción mínima tangible

Conectar primero Google Drive y validar una tarea de solo lectura: localizar un documento conocido y confirmar que Notebookvibes puede proponerlo como fuente sin guardarlo automáticamente.
