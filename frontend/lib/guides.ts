// Step-by-step usage guides shown in place on every page (component <Guide id="..."/>) and in the help centre.
// Every new feature must add its guide here (es + en).

export type Bi = { es: string; en: string };
export type GuideDef = { title: Bi; intro: Bi; steps: Bi[]; tips?: Bi[]; links?: { href: string; label: Bi }[]; roles?: string[] };

const b = (es: string, en: string): Bi => ({ es, en });

export const GUIDES: Record<string, GuideDef> = {
  welcome: {
    title: b("Primeros pasos", "Getting started"),
    intro: b("Spark AI Hub es tu nube de IA privada en el DGX Spark. Todo se ejecuta aquí: tus chats y documentos no salen de este equipo.",
      "Spark AI Hub is your private AI cloud on the DGX Spark. Everything runs here: your chats and documents never leave this machine."),
    steps: [
      b("Cambia tu contraseña en Ajustes → Preferencias si te la dio un administrador.", "Change your password in Settings → Preferences if an administrator gave it to you."),
      b("Abre el Chat, elige un modelo y escribe. Puedes adjuntar imágenes, PDF, Word, Excel o código con el clip 📎.", "Open Chat, choose a model and type. Attach images, PDF, Word, Excel or code with the 📎 clip."),
      b("Pide imágenes en lenguaje natural («dibuja un logo…») o usa el modo Imagen 🎨 del chat.", "Ask for images in plain language (“draw a logo…”) or use the chat's Image 🎨 mode."),
      b("Sube tus documentos a Mis archivos: quedan indexados en «Mis documentos» y el chat puede buscarlos.", "Upload documents to My files: they are indexed in “My documents” and the chat can search them."),
      b("Crea un Proyecto para agrupar chats, documentos e instrucciones de un mismo trabajo y compártelo con compañeros.", "Create a Project to group chats, documents and instructions of one piece of work, and share it with colleagues."),
      b("¿Quieres usar los modelos desde VS Code, OpenCode, Claude Code o Python? Ve a Conectar herramientas.", "Want to use the models from VS Code, OpenCode, Claude Code or Python? Go to Connect tools."),
    ],
    tips: [b("Pulsa «¿Cómo se usa?» en cualquier página para ver su guía. Todas están en Ayuda.", "Click “How does it work?” on any page to see its guide. All guides are in Help.")],
    links: [{ href: "/playground", label: b("Ir al chat", "Go to chat") }, { href: "/connect", label: b("Conectar herramientas", "Connect tools") }, { href: "/help", label: b("Ayuda", "Help") }],
  },
  chat: {
    title: b("Chat", "Chat"),
    intro: b("Conversa con los modelos del Spark. El asistente conoce tus instrucciones y tu memoria, puede leer tus adjuntos, buscar en tus documentos, consultar datos y crear o editar imágenes.",
      "Chat with the Spark models. The assistant knows your instructions and memory, reads attachments, searches your documents, queries data and creates or edits images."),
    steps: [
      b("Elige el modelo arriba. Etiquetas: «vision» = ve imágenes; «tools» = puede usar herramientas (imágenes, documentos, datos). Recomendado: qwen3.5-35b-a3b (visión + herramientas).", "Choose the model at the top. Tags: “vision” = sees images; “tools” = can use tools (images, documents, data). Recommended: qwen3.5-35b-a3b (vision + tools)."),
      b("Adjunta archivos con 📎, arrastrándolos o pegando una imagen (Ctrl+V). PDF, Word, PowerPoint, Excel, CSV, código, imágenes…", "Attach files with 📎, drag and drop, or paste an image (Ctrl+V). PDF, Word, PowerPoint, Excel, CSV, code, images…"),
      b("Para crear una imagen pídelo («genera una foto de…») o activa el modo 🎨 Imagen: tu mensaje será el prompt. Con una imagen adjunta, el modo Imagen la edita.", "To create an image just ask (“generate a photo of…”) or switch on 🎨 Image mode: your message becomes the prompt. With an attached image, Image mode edits it."),
      b("Para editar una imagen del chat di qué cambiar («en img2 pon el cielo de noche») o pulsa «Editar» bajo la imagen.", "To edit an image of the chat say what to change (“make the sky in img2 a night sky”) or click “Edit” under the image."),
      b("Pregunta por tus documentos («¿qué dice mi informe sobre…?»): el asistente busca en tu base de conocimiento y cita archivo y página.", "Ask about your documents (“what does my report say about…?”): the assistant searches your knowledge base and cites file and page."),
      b("Con un Excel/CSV adjunto pregunta en lenguaje natural («ventas por mes en 2025»): el asistente escribe y ejecuta SQL y te muestra la tabla.", "With an Excel/CSV attached ask in plain language (“sales per month in 2025”): the assistant writes and runs SQL and shows the table."),
      b("Dile «recuerda que…» y lo guardará en tu Memoria para próximas conversaciones.", "Say “remember that…” and it is saved in your Memory for future chats."),
      b("Pide que busque en internet («busca las últimas novedades de…», «¿qué precio tiene…?»): consulta varias fuentes, lee las páginas y cita los enlaces. El interruptor «Internet» bajo el mensaje lo activa o desactiva para ese chat.", "Ask it to search the web (“find the latest news on…”, “how much does … cost?”): it checks several sources, reads the pages and cites the links. The “Web” switch under the message turns it on or off for that chat."),
      b("Para tareas largas pide agentes: «lanza un investigador para … y otro para …». Trabajan en paralelo y verás sus pasos en directo; el asistente une sus informes.", "For long tasks ask for agents: “run one researcher for … and another for …”. They work in parallel and you see their steps live; the assistant merges their reports."),
      b("Si la tarea es especializada («ayúdame a escribir un documento técnico»), el asistente busca una skill (instrucciones expertas), la instala y la sigue. Las verás en la página Skills.", "For specialised tasks (“help me write a technical document”) the assistant finds a skill (expert instructions), installs it and follows it. You will see them on the Skills page."),
      b("El indicador «Contexto 8k/256k» de arriba muestra cuánto de la conversación ve el modelo. Púlsalo para cambiar el tamaño de la ventana, activar la compactación automática o «Compactar ahora».", "The “Context 8k/256k” indicator at the top shows how much of the conversation the model sees. Click it to change the window size, turn on automatic compaction or “Compact now”."),
      b("Cálculos, gráficos y archivos: pide «calcula…», «haz un gráfico de…» o «créame un Excel/Word/PowerPoint con…». El asistente escribe y ejecuta código en un entorno aislado (sin internet); verás el código, la salida y los archivos para descargar. También quedan en Mis archivos → «Resultados de código».", "Calculations, charts and files: ask “calculate…”, “plot…” or “create an Excel/Word/PowerPoint with…”. The assistant writes and runs code in an isolated sandbox (no internet); you see the code, the output and the files to download. They are also in My files → “Code results”."),
      b("Lienzo: pide «escríbelo en el lienzo» (informes, cartas, artículos, código). Se abre a la derecha: puedes editarlo, ver versiones, pedir cambios abajo («hazlo más formal») y exportarlo a Word, PDF, Markdown o HTML.", "Canvas: ask “write it in the canvas” (reports, letters, articles, code). It opens on the right: edit it, see versions, ask for changes below (“make it more formal”) and export to Word, PDF, Markdown or HTML."),
      b("Investigación a fondo: activa el interruptor bajo el mensaje y pregunta. Varios agentes investigan en paralelo (unos minutos) y recibes un informe con fuentes numeradas en el lienzo, listo para exportar.", "Deep research: turn on the switch under the message and ask. Several agents research in parallel (a few minutes) and you get a report with numbered sources in the canvas, ready to export."),
      b("Voz: pulsa 🎤 para dictar (se transcribe en el Spark, no en internet) y «Escuchar» bajo una respuesta para oírla. «Leer respuestas» las lee solas al terminar.", "Voice: press 🎤 to dictate (transcribed on the Spark, not on the internet) and “Listen” under an answer to hear it. “Read answers” reads them automatically."),
      b("Tareas: di «cada lunes a las 9, resúmeme…» y el asistente crea una tarea programada (página Tareas).", "Tasks: say “every Monday at 9, summarise…” and the assistant creates a scheduled task (Tasks page)."),
    ],
    tips: [
      b("El modelo marcado con ★ es el general: está siempre cargado para todos y responde al instante. Si eliges otro que no está en memoria, verás cuánta memoria necesita y qué modelos inactivos se descargarían antes de confirmar.", "The model marked ★ is the general one: always loaded for everyone, it answers instantly. If you pick another that is not in memory, you see the memory it needs and which idle models would be unloaded before confirming."),
      b("Puedes irte a otra página mientras el chat piensa o genera una imagen: sigue trabajando. Arriba verás «1 chat trabajando» y un aviso cuando termine; al volver, la conversación muestra el progreso y el resultado.", "You can leave the page while the chat thinks or generates an image: it keeps working. The top bar shows “1 chat working” and a notice when it finishes; when you come back the conversation shows the progress and the result."),
      b("«Pensar» activa el razonamiento paso a paso (más lento, mejor para problemas difíciles).", "“Think” enables step-by-step reasoning (slower, better for hard problems)."),
      b("Busca en todas tus conversaciones con la lupa de la columna izquierda.", "Search all your conversations with the magnifier in the left column."),
      b("Compactar: cuando la conversación llena la mitad de la ventana, los mensajes antiguos se resumen (verás la tarjeta «Contexto compactado»). No se borra nada: el resumen se puede ver u olvidar desde el indicador de contexto.", "Compaction: when the conversation fills half of the window, old messages are summarised (you see a “Context compacted” card). Nothing is deleted: view or forget the summary from the context indicator."),
      b("Privacidad en internet: solo sale la consulta de búsqueda (sin tu nombre ni tus documentos). Si no quieres que salga nada, apaga «Internet».", "Web privacy: only the search query leaves (no name, no documents). If you want nothing to leave, turn “Web” off."),
    ],
  },
  skills: {
    title: b("Skills", "Skills"),
    intro: b("Una skill es un paquete de instrucciones expertas (un SKILL.md y archivos de referencia) que el asistente carga cuando una tarea lo necesita: escribir documentos, preparar presentaciones, seguir un procedimiento del laboratorio…",
      "A skill is a package of expert instructions (a SKILL.md and reference files) the assistant loads when a task needs it: writing documents, preparing presentations, following a lab procedure…"),
    steps: [
      b("Pestaña «Catálogo»: elige una skill y pulsa Instalar. El catálogo viene de repositorios de GitHub que configura el administrador (por defecto anthropics/skills).", "“Catalog” tab: pick a skill and click Install. The catalog comes from GitHub repositories chosen by the administrator (anthropics/skills by default)."),
      b("Pestaña «Instalar desde enlace»: pega la URL de GitHub de la carpeta que contiene el SKILL.md (…/tree/main/skills/nombre), la URL del propio SKILL.md o dueño/repo/ruta.", "“Install from link” tab: paste the GitHub URL of the folder with the SKILL.md (…/tree/main/skills/name), the SKILL.md URL itself, or owner/repo/path."),
      b("También puedes crear la tuya: escribe en el SKILL.md para qué sirve (primera línea) y los pasos. Ideal para procedimientos, plantillas o criterios de tu grupo.", "You can also create your own: write in the SKILL.md what it is for (first line) and the steps. Ideal for your group's procedures, templates or criteria."),
      b("En el chat no hace falta hacer nada: el asistente ve la lista de tus skills y carga la adecuada. Puedes pedirlo explícitamente («usa la skill doc-coauthoring»).", "In the chat you do not need to do anything: the assistant sees your skills and loads the right one. You can also ask for it explicitly (“use the doc-coauthoring skill”)."),
      b("El asistente también puede buscar e instalar skills por sí solo; aparecen con la etiqueta «instalada por el asistente». Desactívalas con el interruptor o bórralas si no te convencen.", "The assistant can also find and install skills by itself; they are tagged “installed by the assistant”. Switch them off or delete them if you do not like them."),
    ],
    tips: [
      b("Las skills son solo texto: sus scripts se guardan como referencia y nunca se ejecutan en el Spark. Revisa las de fuentes desconocidas, porque el asistente seguirá sus instrucciones.", "Skills are text only: their scripts are kept as reference and never executed on the Spark. Review those from unknown sources, as the assistant will follow their instructions."),
      b("Administradores y gestores pueden publicar una skill «para todos» y así todos la tienen disponible.", "Administrators and managers can publish a skill “for everyone” so everyone has it."),
    ],
    links: [{ href: "/agents", label: b("Agentes", "Agents") }, { href: "/playground", label: b("Ir al chat", "Go to chat") }],
  },
  agents: {
    title: b("Agentes", "Agents"),
    intro: b("Los agentes son ayudantes a los que el chat delega una tarea completa. Cada uno trabaja con su propio contexto y sus herramientas, y devuelve un informe. Varios pueden trabajar a la vez.",
      "Agents are helpers the chat delegates a whole task to. Each works with its own context and tools and returns a report. Several can work at once."),
    steps: [
      b("Incluidos: investigador (internet), documentalista (tus documentos), analista (archivos de datos con SQL), revisor (crítica de textos o planes) y general.", "Built-in: researcher (web), librarian (your documents), analyst (data files with SQL), reviewer (critique of texts or plans) and general."),
      b("Úsalos desde el chat pidiéndolo en lenguaje natural: «lanza dos investigadores en paralelo: uno sobre … y otro sobre …; luego compáralos».", "Use them from the chat in plain language: “run two researchers in parallel: one on … and one on …; then compare them”."),
      b("Mientras trabajan verás cada agente en una tarjeta con sus pasos en directo; al terminar, despliega «Informe del agente» para leer lo que encontró y sus fuentes.", "While they work each agent shows a card with its live steps; when done, open “Agent report” to read what it found and its sources."),
      b("Crea los tuyos con «Nuevo agente»: nombre, para qué sirve (el asistente lo lee para decidir cuándo usarlo), instrucciones, herramientas permitidas y, si quieres, una skill precargada.", "Create your own with “New agent”: name, what it is for (the assistant reads it to decide when to use it), instructions, allowed tools and optionally a preloaded skill."),
    ],
    tips: [
      b("Los agentes no ven tu conversación: el asistente les pasa el contexto necesario. Si quieres algo concreto, díselo en el mensaje.", "Agents do not see your conversation: the assistant passes them the context they need. If you want something specific, say it in your message."),
      b("Usan el modelo general, así que no ocupan memoria extra. El administrador fija cuántos trabajan a la vez (3 por defecto).", "They use the general model, so they need no extra memory. The administrator sets how many run at once (3 by default)."),
    ],
    links: [{ href: "/skills", label: b("Skills", "Skills") }, { href: "/playground", label: b("Ir al chat", "Go to chat") }],
  },
  projects: {
    title: b("Proyectos", "Projects"),
    intro: b("Un proyecto agrupa chats, documentos e instrucciones de un mismo trabajo (una tesis, un artículo, una asignatura). Todo chat dentro del proyecto usa sus instrucciones y puede buscar en sus documentos.",
      "A project groups the chats, documents and instructions of one piece of work (a thesis, a paper, a course). Every chat in the project uses its instructions and can search its documents."),
    steps: [
      b("Pulsa «Nuevo proyecto», ponle nombre y escribe instrucciones (p. ej. «Responde como revisor de artículos de ingeniería, en inglés»).", "Click “New project”, name it and write instructions (e.g. “Answer as a reviewer of engineering papers, in English”)."),
      b("Abre el proyecto y sube sus documentos en «Documentos del proyecto»: se indexan automáticamente.", "Open the project and upload its documents under “Project documents”: they are indexed automatically."),
      b("Pulsa «Nuevo chat en el proyecto». Las conversaciones quedan guardadas dentro del proyecto.", "Click “New chat in project”. Conversations are saved inside the project."),
      b("Comparte el proyecto con compañeros por su usuario: «lectura» (pueden chatear con los documentos) o «escritura» (también añadir documentos y cambiar instrucciones).", "Share the project with colleagues by username: “read” (chat with the documents) or “write” (also add documents and change instructions)."),
    ],
    tips: [b("Los chats son siempre personales: tus compañeros ven los documentos e instrucciones del proyecto, no tus conversaciones.", "Chats are always personal: colleagues see the project's documents and instructions, not your conversations.")],
  },
  files: {
    title: b("Mis archivos", "My files"),
    intro: b("Tu disco privado en el Spark. Los documentos se convierten a texto y se indexan para que el chat y tus herramientas puedan usarlos.",
      "Your private drive on the Spark. Documents are converted to text and indexed so that the chat and your tools can use them."),
    steps: [
      b("Pulsa «Subir» o arrastra archivos a la página. Puedes elegir carpeta y si se añaden a la base de conocimiento.", "Click “Upload” or drag files onto the page. Choose a folder and whether they are added to the knowledge base."),
      b("Formatos: PDF, Word (.docx), PowerPoint (.pptx), Excel (.xlsx), CSV/TSV, JSON, Parquet, SQLite, Markdown, HTML, código, imágenes y audio. No se aceptan ejecutables ni archivos pickle.", "Formats: PDF, Word (.docx), PowerPoint (.pptx), Excel (.xlsx), CSV/TSV, JSON, Parquet, SQLite, Markdown, HTML, code, images and audio. Executables and pickle files are rejected."),
      b("La columna «Texto» indica si se pudo extraer: ✓ listo, ⏳ procesando, ✗ sin texto (p. ej. PDF escaneado: adjúntalo al chat con un modelo con visión).", "The “Text” column shows whether text was extracted: ✓ ready, ⏳ processing, ✗ no text (e.g. a scanned PDF: attach it to a chat with a vision model)."),
      b("Desde cada archivo: «Preguntar en el chat» abre una conversación con él adjunto.", "From each file: “Ask in chat” opens a conversation with it attached."),
    ],
    tips: [b("Tu cuota de espacio aparece arriba. Los archivos de datos (CSV, Excel, SQLite) se consultan con SQL desde el chat.", "Your storage quota is shown at the top. Data files (CSV, Excel, SQLite) are queried with SQL from the chat.")],
  },
  workspace: {
    title: b("Workspace", "Workspace"),
    intro: b("Tu espacio de trabajo remoto en el Spark: una carpeta que se conserva, con editor, terminal y un asistente que escribe, ejecuta y corrige código sobre tus archivos. Todo se ejecuta aislado y sin internet.",
      "Your remote workspace on the Spark: a persistent folder with an editor, a terminal and an assistant that writes, runs and fixes code on your files. Everything runs isolated and without internet."),
    steps: [
      b("Sube tus datos con ⬆ (o arrástralos a la columna de archivos), o tráelos desde Mis archivos con el icono del disco.", "Upload your data with ⬆ (or drop it on the file column), or bring it from My files with the drive icon."),
      b("Pídele al asistente (derecha) lo que necesites: «con ventas.csv hazme un Excel con un resumen por mes y un gráfico», «genera un PDF con…», «crea un script que…». Escribe el código, lo ejecuta y corrige los errores él solo.", "Ask the assistant (right) what you need: “from sales.csv make an Excel with a monthly summary and a chart”, “make a PDF with…”, “write a script that…”. It writes the code, runs it and fixes errors on its own."),
      b("Abre cualquier archivo: el código se edita (Ctrl+S guarda) y se ejecuta con ▶; los Excel/CSV se ven como tabla; PDF, imágenes y HTML se previsualizan.", "Open any file: code is editable (Ctrl+S saves) and runs with ▶; Excel/CSV show as a table; PDF, images and HTML are previewed."),
      b("Terminal (abajo): ejecuta tus propios comandos (python, ls, pandoc, soffice…). ↑ recupera los anteriores.", "Terminal (bottom): run your own commands (python, ls, pandoc, soffice…). ↑ recalls previous ones."),
      b("Descarga un archivo con ⬇, todo el workspace en ZIP, o guarda una copia en Mis archivos.", "Download a file with ⬇, the whole workspace as ZIP, or save a copy to My files."),
    ],
    tips: [b("Puedes tener varios workspaces (uno por proyecto). Cada uno tiene su propio chat; «nueva conversación» empieza de cero sin borrar archivos.", "You can have several workspaces (one per project). Each has its own chat; “new conversation” starts fresh without deleting files."),
      b("Sin internet: no se pueden instalar paquetes nuevos (pip/npm). Ya incluye pandas, numpy, matplotlib, scikit-learn, openpyxl, python-docx, reportlab, LibreOffice y pandoc.", "No internet: new packages cannot be installed (pip/npm). It already includes pandas, numpy, matplotlib, scikit-learn, openpyxl, python-docx, reportlab, LibreOffice and pandoc.")],
  },
  tasks: {
    title: b("Tareas programadas", "Scheduled tasks"),
    intro: b("El asistente trabaja solo a la hora que digas: resúmenes de novedades, vigilancia de webs, informes periódicos… Cada tarea tiene su propio chat con todos los resultados.", "The assistant works on its own on schedule: news digests, monitoring, periodic reports… Each task has its own chat with all results."),
    steps: [
      b("Pulsa «Nueva tarea» (o usa un ejemplo) y escribe las instrucciones completas, como si fuera un mensaje nuevo cada vez.", "Click “New task” (or use an example) and write the full instructions, as if it were a new message each time."),
      b("Elige cuándo: cada hora, días de la semana a una hora, un día de la semana o un día del mes.", "Choose when: hourly, some weekdays at a time, one weekday or one day of the month."),
      b("Activa «Puede buscar en internet» si necesita información actual, e «Investigación a fondo» si quieres un informe completo.", "Turn on “May search the web” for current information, and “Deep research” for a full report."),
      b("Pulsa «Ejecutar ahora» para probarla. Cuando termina recibes un aviso en la campana (y por correo si lo marcas). «Ver resultados» abre su chat.", "Click “Run now” to test it. When it finishes you get a bell notice (and an e-mail if ticked). “Results” opens its chat."),
    ],
    tips: [b("También puedes crearlas desde el chat: «todos los días laborables a las 8:30, busca noticias sobre … y resúmelas».", "You can also create them from the chat: “every weekday at 8:30, find news about … and summarise them”."),
      b("Para recibir el correo, un administrador debe configurar el correo saliente (Ajustes → Acceso y correo).", "To receive e-mails, an administrator must configure outgoing mail (Settings → Access and mail).")],
  },
  usage: {
    title: b("Uso", "Usage"),
    intro: b("Cuánto usas el Spark: tokens leídos y generados, modelos, proyectos, trabajos de imagen/vídeo/audio y almacenamiento.", "How much you use the Spark: input and output tokens, models, projects, image/video/audio jobs and storage."),
    steps: [
      b("Elige el periodo arriba a la derecha (7, 30, 90 días o un año).", "Pick the period at the top right (7, 30, 90 days or a year)."),
      b("«Tokens este mes» muestra tu cuota mensual si el administrador la ha fijado.", "“Tokens this month” shows your monthly quota if the administrator set one."),
      b("Administradores y gestores pueden ver «Todo el equipo» con el uso por persona (solo cifras, nunca el contenido).", "Administrators and managers can see “Everyone” with usage per person (numbers only, never content)."),
    ],
  },
  knowledge: {
    title: b("Base de conocimiento", "Knowledge base"),
    intro: b("Colecciones de documentos indexados para búsqueda semántica (por significado) y por palabras. El chat (search_knowledge) y el servidor MCP buscan aquí y citan la fuente.",
      "Collections of documents indexed for semantic (meaning) and keyword search. The chat (search_knowledge) and the MCP server search here and cite the source."),
    steps: [
      b("Tienes una colección «Mis documentos» por defecto; crea más para separar temas (p. ej. «Normativa», «Artículos 2025»).", "You have a default “My documents” collection; create more to separate topics (e.g. “Regulations”, “Papers 2025”)."),
      b("Añade documentos desde Mis archivos o subiéndolos aquí.", "Add documents from My files or by uploading them here."),
      b("Prueba la búsqueda en «Probar búsqueda» para ver qué fragmentos encontraría el asistente.", "Try “Test search” to see which passages the assistant would find."),
      b("En el chat pregunta normalmente; el asistente decide cuándo buscar y cita [archivo, p. N].", "In the chat just ask; the assistant decides when to search and cites [file, p. N]."),
      b("Conectores (abajo): trae documentos de Nextcloud/WebDAV, GitHub, Zotero o una carpeta de red. Cada uno explica dónde sacar la contraseña de aplicación, el token o la clave; se sincronizan solos cada hora o cada día.", "Connectors (below): bring documents from Nextcloud/WebDAV, GitHub, Zotero or a network share. Each one explains where to get the app password, token or key; they re-sync hourly or daily."),
    ],
    tips: [b("Las colecciones de proyectos compartidos contigo también aparecen aquí.", "Collections of projects shared with you also appear here.")],
  },
  memory: {
    title: b("Memoria y personalización", "Memory and personalisation"),
    intro: b("Lo que el asistente sabe de ti en todas las conversaciones. Tú decides qué se guarda: puedes verlo, editarlo o borrarlo todo.",
      "What the assistant knows about you in every conversation. You decide what is stored: view, edit or delete everything."),
    steps: [
      b("En «Sobre ti» describe tu rol e intereses (p. ej. «Investigador en diseño industrial, trabajo con Python y Rhino»).", "In “About you” describe your role and interests (e.g. “Industrial design researcher, I work with Python and Rhino”)."),
      b("En «Instrucciones» indica cómo quieres las respuestas (idioma, tono, formato, nivel de detalle).", "In “Instructions” say how you want answers (language, tone, format, level of detail)."),
      b("Las memorias se añaden cuando en el chat dices «recuerda que…» o desde esta página.", "Memories are added when you say “remember that…” in the chat, or from this page."),
      b("Desactiva la memoria con el interruptor si no quieres que se use ni se guarde.", "Switch memory off if you do not want it used or stored."),
    ],
  },
  discover: {
    title: b("Descubrir e instalar modelos", "Discover and install models"),
    intro: b("Busca modelos en Hugging Face. Para cada uno verás si es compatible con el Spark, cuánta memoria y disco necesita y con qué motor se ejecutaría.",
      "Search models on Hugging Face. For each one you see whether it is compatible with the Spark, how much memory and disk it needs and which engine runs it."),
    steps: [
      b("Busca por nombre o tarea (p. ej. «qwen coder», «whisper», «embedding») o usa «Recomendaciones».", "Search by name or task (e.g. “qwen coder”, “whisper”, “embedding”) or use “Recommendations”."),
      b("Abre un modelo y revisa el veredicto (verificado, probable, experimental…), la memoria estimada y la licencia.", "Open a model and check the verdict (verified, likely, experimental…), estimated memory and licence."),
      b("Pulsa «Instalar». Si eres investigador se envía una solicitud: escribe para qué lo necesitas. Un administrador o gestor la aprobará.", "Click “Install”. As a researcher this sends a request: say what you need it for. An administrator or manager approves it."),
      b("Te llegará una notificación (🔔 y correo) al aprobarse y cuando el modelo esté listo.", "You get a notification (🔔 and e-mail) when it is approved and when the model is ready."),
    ],
    links: [{ href: "/requests", label: b("Ver mis solicitudes", "See my requests") }],
  },
  requests: {
    title: b("Solicitudes de modelos", "Model requests"),
    intro: b("Aquí ves el estado de los modelos que has pedido. Administradores y gestores revisan y deciden.",
      "Here you see the status of the models you asked for. Administrators and managers review and decide."),
    steps: [
      b("Investigadores: cada solicitud muestra estado (pendiente, aprobada, rechazada) y el comentario del revisor.", "Researchers: each request shows its status (pending, approved, rejected) and the reviewer's comment."),
      b("Revisores: comprueba memoria estimada, tamaño y licencia; elige si el modelo será para todos (público) o solo para quien lo pidió, y aprueba o rechaza con un comentario.", "Reviewers: check estimated memory, size and licence; choose whether the model is for everyone (public) or only the requester, and approve or reject with a comment."),
      b("La descarga empieza al aprobar; su progreso se ve en Biblioteca y Trabajos.", "The download starts on approval; progress is shown in Library and Jobs."),
    ],
  },
  library: {
    title: b("Biblioteca de modelos", "Model library"),
    intro: b("Modelos instalados a los que tienes acceso, con su estado, memoria y motor.", "Installed models you can access, with status, memory and engine."),
    steps: [
      b("«Cargar» pone el modelo en memoria; «Descargar de memoria» la libera. No hace falta: el chat y la API los cargan solos.", "“Load” puts the model in memory; “Unload” frees it. Not required: chat and API load them automatically."),
      b("Abre un modelo para ver detalles, pruebas, historial y parámetros de ejecución (propietarios y administradores).", "Open a model to see details, tests, history and runtime parameters (owners and administrators)."),
      b("«Ventana de contexto» (en la ficha de un modelo vLLM): elige 32k, 64k, 128k, 256k… y pulsa «Aplicar». Cada opción dice cuántas conversaciones de ese tamaño caben a la vez. El modelo se recarga en unos minutos; después vuelve a copiar la configuración de OpenCode/Cline en «Conectar».", "“Context window” (on a vLLM model's page): pick 32k, 64k, 128k, 256k… and click “Apply”. Each option says how many conversations of that size fit at once. The model reloads in a few minutes; then copy the OpenCode/Cline configuration again from “Connect”."),
    ],
    tips: [b("El panel «Memoria del Spark» muestra en tiempo real qué ocupa la memoria: el modelo general (siempre cargado), los modelos extra con su cuenta atrás de descarga automática (5 min sin uso), lo que se está cargando con su progreso y los trabajos de imagen/vídeo.", "The “Spark memory” panel shows live what uses the memory: the general model (always loaded), extra models with their auto-unload countdown (5 idle min), what is loading with its progress, and image/video jobs."),
           b("«Cargar» te enseña antes cuánta memoria necesita, cuánto tardará y qué modelos inactivos se descargarían. La barra de memoria también está arriba a la derecha en todas las páginas.", "“Load” first shows the memory it needs, how long it takes and which idle models would be unloaded. The memory bar is also at the top right of every page.")],
  },
  compare: {
    title: b("Comparar modelos", "Compare models"),
    intro: b("Envía el mismo prompt a varios modelos y compara respuestas, tiempo hasta el primer token y velocidad.", "Send the same prompt to several models and compare answers, time to first token and speed."),
    steps: [b("Escribe el prompt y elige 2–4 modelos.", "Write the prompt and choose 2–4 models."), b("Se ejecutan uno tras otro (para no saturar la memoria) y verás los resultados lado a lado.", "They run one after another (to protect memory) and results appear side by side.")],
  },
  benchmarks: {
    title: b("Benchmarks", "Benchmarks"),
    intro: b("Pruebas reproducibles de rendimiento y calidad sobre un modelo.", "Reproducible performance and quality tests on a model."),
    steps: [b("Elige modelo y suite (rendimiento, aritmética exacta, JSON, test de opción múltiple).", "Choose model and suite (performance, exact arithmetic, JSON, multiple-choice)."), b("Los resultados se guardan con el entorno (GPU, versión, cuantización) y se pueden exportar a CSV.", "Results are stored with the environment (GPU, version, quantisation) and can be exported to CSV.")],
  },
  image: {
    title: b("Image Studio", "Image Studio"),
    intro: b("Generación y edición de imágenes con flujos ComfyUI verificados. También puedes pedir imágenes directamente en el chat.", "Image generation and editing with verified ComfyUI workflows. You can also ask for images directly in the chat."),
    steps: [
      b("Elige un flujo: Qwen-Image (alta calidad, texto legible en la imagen), SD 1.5 (rápido), edición con instrucciones (Qwen-Image-Edit), img2img, inpainting o escalado ×4.", "Choose a workflow: Qwen-Image (high quality, readable text), SD 1.5 (fast), instruction editing (Qwen-Image-Edit), img2img, inpainting or ×4 upscale."),
      b("Escribe el prompt (mejor en inglés y detallado), ajusta tamaño y semilla y pulsa Generar.", "Write the prompt (best in English and detailed), adjust size and seed and click Generate."),
      b("Cada imagen guarda sus parámetros y el flujo exacto para reproducirla.", "Each image keeps its parameters and the exact workflow to reproduce it."),
    ],
  },
  video: {
    title: b("Video Studio", "Video Studio"),
    intro: b("Texto a vídeo (Wan 2.1) e imagen a vídeo (Wan 2.2 si está instalado). Son trabajos pesados que se encolan.", "Text to video (Wan 2.1) and image to video (Wan 2.2 if installed). Heavy jobs that are queued."),
    steps: [b("Escribe el prompt y elige resolución, número de fotogramas y fps.", "Write the prompt and choose resolution, frames and fps."), b("Sigue el progreso en Trabajos; el resultado es un WebP animado descargable.", "Follow progress in Jobs; the result is a downloadable animated WebP.")],
  },
  audio: {
    title: b("Audio Studio", "Audio Studio"),
    intro: b("Transcripción de audio (Whisper) y síntesis de voz.", "Audio transcription (Whisper) and speech synthesis."),
    steps: [b("Transcribir: sube un audio (wav, mp3, m4a, flac, ogg) y elige idioma o detección automática. Obtienes TXT, JSON y subtítulos SRT.", "Transcribe: upload audio (wav, mp3, m4a, flac, ogg) and choose language or auto-detect. You get TXT, JSON and SRT subtitles."), b("Voz: escribe un texto y elige el modelo de voz.", "Speech: write text and choose the voice model.")],
  },
  science: {
    title: b("Laboratorio científico", "Scientific lab"),
    intro: b("Datasets, modelos científicos con adaptadores de confianza (sin código remoto ni pickle) y experimentos reproducibles.", "Datasets, scientific models with trusted adapters (no remote code or pickle) and reproducible experiments."),
    steps: [b("Sube un dataset (CSV, JSON, NPY o ZIP de imágenes).", "Upload a dataset (CSV, JSON, NPY or image ZIP)."), b("Registra un modelo (safetensors) con un adaptador (regresión lineal, clasificador de imágenes).", "Register a model (safetensors) with an adapter (linear regression, image classifier)."), b("Crea un experimento y ejecútalo: obtendrás métricas y predicciones descargables.", "Create an experiment and run it: you get metrics and downloadable predictions.")],
  },
  jobs: {
    title: b("Trabajos", "Jobs"),
    intro: b("Todo lo que tarda (descargas, imágenes, vídeos, indexado, benchmarks) se ejecuta como trabajo en segundo plano.", "Everything that takes time (downloads, images, videos, indexing, benchmarks) runs as a background job."),
    steps: [b("Mira estado, progreso y eventos; si espera, el motivo aparece (cola, límite de concurrencia, memoria).", "See status, progress and events; if it is waiting, the reason is shown (queue, concurrency limit, memory)."), b("Puedes cancelar tus trabajos; los resultados quedan en «Artefactos».", "You can cancel your jobs; results stay in “Artifacts”.")],
  },
  api: {
    title: b("Centro API", "API centre"),
    intro: b("Claves de API para usar los modelos desde tus programas y herramientas (compatible con OpenAI y Anthropic) y tu consumo.", "API keys to use the models from your programs and tools (OpenAI and Anthropic compatible) and your usage."),
    steps: [
      b("Pulsa «Nueva clave», ponle un nombre (p. ej. «OpenCode portátil») y una caducidad.", "Click “New key”, name it (e.g. “OpenCode laptop”) and set an expiry."),
      b("Cópiala en ese momento: solo se muestra una vez. Se abrirán las instrucciones para tu herramienta con la clave ya puesta.", "Copy it right away: it is shown only once. Instructions for your tool open with the key already filled in."),
      b("Guárdala como variable de entorno (SPARK_API_KEY) o en la configuración de la herramienta, nunca en un repositorio git.", "Store it as an environment variable (SPARK_API_KEY) or in the tool's settings, never in a git repository."),
      b("Revoca la clave si la pierdes o cambias de ordenador.", "Revoke the key if you lose it or change computer."),
    ],
    links: [{ href: "/connect", label: b("Instrucciones por herramienta", "Per-tool instructions") }],
  },
  connect: {
    title: b("Conectar herramientas", "Connect tools"),
    intro: b("Usa los modelos y tu nube personal desde tu editor o agente favorito: OpenCode, Claude Code, Continue, Cline, Aider, Cursor, Python… Elige la herramienta y tu sistema operativo y sigue los pasos.",
      "Use the models and your personal cloud from your favourite editor or agent: OpenCode, Claude Code, Continue, Cline, Aider, Cursor, Python… Pick the tool and your operating system and follow the steps."),
    steps: [
      b("Requisito: tu ordenador debe tener Tailscale conectado (como para abrir esta web).", "Requirement: your computer must have Tailscale connected (as for opening this website)."),
      b("Crea una clave con el botón de esta página: se rellenará en todos los ejemplos.", "Create a key with this page's button: it is filled into every example."),
      b("Elige herramienta y sistema operativo, copia la configuración en el archivo indicado y reinicia la herramienta.", "Choose tool and operating system, copy the configuration into the file shown and restart the tool."),
      b("Ejecuta la comprobación final: si responde, está todo bien.", "Run the final check: if it answers, everything is fine."),
    ],
    tips: [b("¿Tu agente se queda sin contexto? La configuración ya incluye la ventana real del modelo; en OpenCode escribe /compact para resumir la sesión. Si sigue quedándose corta, un administrador puede ampliarla en Biblioteca → (modelo) → «Ventana de contexto».", "Your agent runs out of context? The configuration already includes the model's real window; in OpenCode type /compact to summarise the session. If it is still too short, an administrator can raise it in Library → (model) → “Context window”.")],
  },
  storage: {
    title: b("Almacenamiento", "Storage"),
    intro: b("Espacio en disco del Spark y tu consumo personal.", "Spark disk space and your personal usage."),
    steps: [b("Tu cuota incluye archivos, datasets y resultados generados.", "Your quota includes files, datasets and generated results."), b("Borra resultados antiguos desde Trabajos o Mis archivos si te acercas al límite.", "Delete old results from Jobs or My files if you approach the limit.")],
  },
  users: {
    title: b("Usuarios y permisos", "Users and permissions"),
    intro: b("Alta de personas, solicitudes de cuenta, roles y cuotas.", "Onboarding, account requests, roles and quotas."),
    roles: ["admin", "manager"],
    steps: [
      b("Invitar: «Invitar usuario» → usuario, nombre, email y rol. Si el correo está configurado se envía la invitación con los pasos de Tailscale; si no, copia el enlace y envíalo tú.", "Invite: “Invite user” → username, name, e-mail and role. If mail is configured the invitation is sent with the Tailscale steps; otherwise copy the link and send it yourself."),
      b("Solicitudes: en la pestaña «Solicitudes de cuenta» aprueba (eligiendo rol) o rechaza a quien se ha registrado.", "Requests: in the “Account requests” tab approve (choosing a role) or reject people who signed up."),
      b("Roles: admin (todo), gestor (usuarios, solicitudes y modelos; sin ajustes de sistema), investigador (uso completo), lector (solo chat/consulta).", "Roles: admin (everything), manager (users, requests and models; no system settings), researcher (full use), viewer (chat/read only)."),
      b("Políticas: límites por rol (trabajos simultáneos, cuota de disco, peticiones API, tokens/mes). Solo administradores.", "Policies: limits per role (concurrent jobs, disk quota, API requests, tokens/month). Administrators only."),
    ],
    tips: [b("Antes de invitar, comparte el Spark en Tailscale con esa persona (ver Ajustes → Acceso).", "Before inviting, share the Spark in Tailscale with that person (see Settings → Access).")],
  },
  monitoring: {
    title: b("Monitorización", "Monitoring"),
    intro: b("Uso real de CPU, memoria unificada, GPU, temperaturas, disco y red, y alertas.", "Real CPU, unified memory, GPU, temperature, disk and network usage, and alerts."),
    steps: [b("La memoria de GPU no se muestra por separado: en el GB10 es la misma memoria unificada.", "GPU memory is not shown separately: on the GB10 it is the same unified memory."), b("Las alertas (disco, temperatura, colas) se pueden reconocer.", "Alerts (disk, temperature, queues) can be acknowledged.")],
  },
  settings: {
    title: b("Ajustes", "Settings"),
    intro: b("Preferencias personales, privacidad y, para administradores, sistema, acceso, correo y copias de seguridad.", "Personal preferences, privacy and, for administrators, system, access, mail and backups."),
    steps: [b("Preferencias: idioma, tema, contraseña, avisos por correo y sesiones abiertas.", "Preferences: language, theme, password, e-mail notices and open sessions."), b("Privacidad: descarga todos tus datos en un ZIP o elimina tu cuenta.", "Privacy: download all your data as a ZIP or delete your account.")],
  },
  access: {
    title: b("Acceso: Tailscale, registro y correo", "Access: Tailscale, sign-up and mail"),
    intro: b("Cómo llegan las personas a la plataforma (opción A: compartir el Spark por Tailscale) y cómo reciben correos.", "How people reach the platform (option A: sharing the Spark through Tailscale) and how they receive e-mails."),
    roles: ["admin"],
    steps: [
      b("Correo: servidor SMTP de tu organización (por ejemplo smtp.tu-organizacion.es), puerto 587, STARTTLS, tu usuario de correo y su contraseña; remitente = tu dirección. Pulsa «Enviar prueba».", "Mail: your organisation's SMTP server (e.g. smtp.your-org.com), port 587, STARTTLS, your mail username and password; sender = your address. Click “Send test”."),
      b("Tailscale (manual): en login.tailscale.com → Machines → (tu Spark) → ⋯ → Share → «Copy share link» y pégalo en «Enlace para compartir»: irá en cada invitación.", "Tailscale (manual): at login.tailscale.com → Machines → (your Spark) → ⋯ → Share → “Copy share link” and paste it in “Share link”: it goes into every invitation."),
      b("Tailscale (automático, opcional): crea una API key en Settings → Keys de Tailscale, pégala con el ID del equipo y pulsa «Comprobar»: cada invitación creará una invitación de Tailscale para ese email.", "Tailscale (automatic, optional): create an API key in Tailscale Settings → Keys, paste it with the device ID and click “Check”: every invitation will create a Tailscale invite for that e-mail."),
      b("Seguridad: en la política de Tailscale (Access controls) limita a los invitados al puerto 443 del Spark (ejemplo en la guía PDF del administrador).", "Security: in the Tailscale policy (Access controls) restrict shared users to port 443 of the Spark (example in the admin PDF guide)."),
      b("Registro: «con aprobación» (cualquiera con acceso a la red lo solicita y tú/gestores aprobáis), «abierto» o «cerrado» (solo invitaciones).", "Sign-up: “approval” (anyone with network access requests it and you/managers approve), “open” or “closed” (invitations only)."),
      b("Entrar con Google o Microsoft (opcional): abre la tarjeta del proveedor, sigue sus 3 pasos, copia la dirección de redirección en su consola y pega aquí el ID y el secreto. Aparecerá el botón en la pantalla de entrada.", "Sign in with Google or Microsoft (optional): open the provider card, follow its 3 steps, copy the redirect address into its console and paste the ID and secret here. The button appears on the sign-in page."),
    ],
  },
  android: {
    title: b("App para Android", "Android app"),
    intro: b("La app abre Spark AI Hub adaptado al móvil: chat con fotos de la cámara, archivos y PDF, compartir desde otras apps, proyectos, archivos e imágenes. Los menús cambian según tu rol (administrador, gestor o usuario).",
      "The app opens Spark AI Hub adapted to the phone: chat with camera photos, files and PDFs, sharing from other apps, projects, files and images. Menus change with your role (administrator, manager or user)."),
    steps: [
      b("Instala «Tailscale» desde Google Play, ábrelo e inicia sesión con la misma cuenta con la que aceptaste la invitación al Spark. Pulsa «Connect».", "Install “Tailscale” from Google Play, open it and sign in with the same account you used to accept the Spark invitation. Press “Connect”."),
      b("Con Tailscale conectado, abre en el navegador del móvil la dirección de descarga (o pulsa «Descargar APK») y descarga spark-ai-hub.apk.", "With Tailscale connected, open the download address in the phone browser (or press “Download APK”) and download spark-ai-hub.apk."),
      b("Abre el archivo descargado. Android pedirá permitir «instalar apps de origen desconocido» para tu navegador: permítelo solo para esta instalación y pulsa Instalar.", "Open the downloaded file. Android asks to allow “install unknown apps” for your browser: allow it just for this install and press Install."),
      b("Abre «Spark AI Hub». Si Tailscale no está conectado, la app te lo dirá y el botón «Abrir Tailscale» te lleva a la app de Tailscale (o a Play Store si no la tienes). Al volver se conecta sola.", "Open “Spark AI Hub”. If Tailscale is not connected the app says so and “Open Tailscale” takes you to the Tailscale app (or Play Store if missing). When you come back it connects by itself."),
      b("Inicia sesión con tu usuario y contraseña del Spark AI Hub.", "Sign in with your Spark AI Hub username and password."),
      b("Para enviar una foto o un PDF al chat desde otra app (Galería, Archivos, WhatsApp…), usa «Compartir» → «Spark AI Hub»: se sube a Mis archivos y se abre un chat con el adjunto.", "To send a photo or a PDF to the chat from another app (Gallery, Files, WhatsApp…), use “Share” → “Spark AI Hub”: it is uploaded to My files and a chat opens with the attachment."),
    ],
    tips: [b("Android solo permite una VPN activa: si usas otra VPN, desactívala para usar Tailscale.", "Android allows only one active VPN: if you use another VPN, turn it off to use Tailscale."),
      b("Si cambia la dirección del servidor, en la pantalla de conexión pulsa «Cambiar dirección del servidor».", "If the server address changes, press “Change server address” on the connection screen.")],
  },
  privacy: {
    title: b("Privacidad y tus datos", "Privacy and your data"),
    intro: b("Tus datos son tuyos. Los administradores solo ven metadatos de uso, nunca el contenido de tus chats o archivos.", "Your data is yours. Administrators only see usage metadata, never the content of your chats or files."),
    steps: [b("«Exportar mis datos» genera un ZIP con chats (JSON y Markdown), memoria, proyectos, archivos y resultados. Te avisaremos cuando esté listo.", "“Export my data” creates a ZIP with chats (JSON and Markdown), memory, projects, files and results. You will be notified when it is ready."), b("«Eliminar mi cuenta» borra todo tu contenido de forma permanente y desactiva la cuenta.", "“Delete my account” permanently deletes all your content and deactivates the account.")],
  },
};

export function guideText(g: GuideDef, lang: string): string {
  const L = (x: Bi) => (lang === "en" ? x.en : x.es);
  return [L(g.title), L(g.intro), ...g.steps.map(L), ...(g.tips ?? []).map(L)].join(" ").toLowerCase();
}
