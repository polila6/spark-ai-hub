# Changelog

## 0.19.5 — 2026-11-06
Select several and act on all of them, and the GPU panel works again.

* **Bulk actions** (`POST …/bulk`, shared UI in `components/bulk.tsx`): checkboxes (shift-click selects a range), a bar with the
  actions and a confirmation that names what will be deleted. **Jobs** (cancel the running, delete the finished), **model library**
  (delete, with «also delete the files»; pinned models and the general one are left alone and say so), **My files**,
  **notifications** (new page `/notifications`: mark read/unread, delete, «delete read») and **chats** (a «Seleccionar» mode in the chat
  list and in a project's chats). Every item is checked with the rules of its single endpoint; a refused one is reported with the reason
  and never stops the rest; missing and foreign ids get the same answer; one audit event per call.
* **GPU in the panel**: the monitor container can lose its access to the GPU («Failed to initialize NVML: Unknown Error», the host is
  fine) and the panel then said «no disponible» with an empty reason. The reason is now shown, an alert is raised, and the monitor
  restarts itself after two minutes of it (never in a loop).
* Fix: a stuck «in use» counter could keep an idle embedding model loaded for an hour; cleared by hand this time (structural fix pending).

## 0.19.4 — 2026-11-06
Several models always ready, and the words reach the screen as the model writes them.

* **Keep several models loaded** (a main one for chat, others for a tool such as Hermes): «Mantener siempre cargado» on any model.
  Pinned models are never unloaded for being idle nor evicted to make room, and now **load again by themselves after a restart or a
  crash** (one at a time, protected by the memory guard; before it only the general model did). Pinning is refused up front if the
  pinned models would not fit in memory together, saying how much each reserves and what to lower.
* **Words arrive at the model's pace**: the chat stream polled for new text every 300 ms, so text came in bursts of ~50 characters;
  it now follows every 40 ms while words flow (3 times a second when idle).
* **The director respects «sin buscar nada en internet»** (and «no busques», «without searching the web»): it used to read it as an
  order to search. A web address in the message still lets it read that page.

## 0.19.3 — 2026-11-06
Answers appear at the model's own rhythm, a readable agent menu, and API clients can control reasoning.

* **Streaming like ChatGPT** (`lib/fade-words.ts`): nothing is held back any more. Each word is on screen as soon as the model
  produces it and fades in as it appears; a burst of words is staggered over a fraction of a second instead of popping. This replaces
  the 0.19.2 paced reveal, which lagged behind the model.
* **Agent menu** (`components/agent-picker.tsx`): a menu of its own instead of the browser's select, readable in both themes, with each
  agent's description.
* **`reasoning_effort` is honoured** on `/v1/chat/completions` (and `reasoning: {effort}`): «none»/«minimal» turn a reasoning model's
  thinking off, anything else on. It used to be ignored, so tools like Hermes could not shorten the model's reasoning. New admin
  setting `api_default_thinking` (auto | off | on, Settings → System) for clients that say nothing. An explicit
  `chat_template_kwargs.enable_thinking` always wins.

## 0.19.2 — 2026-11-06
Answers flow instead of popping in.

* **Smooth streaming** (`lib/smooth-text.ts`): the server still sends text in bursts, but the screen reveals it word by word at an even
  pace that speeds up when the backlog grows (it never lags more than about a second) and finishes by itself when the answer ends.
  A soft cursor blinks at the end while it is being written. Applies to the chat, projects and the workspace assistant, and to the
  reasoning panel. Text that is already there (history, a reloaded page) appears at once, and so does everything with
  «reduce motion» enabled in the system.

## 0.19.1 — 2026-11-06
A big model load can no longer take the machine down, and the chat stops rummaging in your files.

* **Memory guard for big loads** (`services/memguard.py`): before loading a large vLLM model the hub pauses running Hugging Face
  downloads (they resume afterwards), frees the robot server's memory, evicts idle models and requires real free memory
  (reservation + `load_safety_gb`, default 8 GB); otherwise it refuses with the numbers. Downloads wait while a large model loads and
  for the first minute after a restart. Triggered by a freeze on 2026-11-06 02:19 (loading a 70 GB model while two downloads ran).
* A load that died for lack of memory is no longer retried every ten minutes by the resident-model loop; administrators are told.
  The error hint no longer blames `trust_remote_code` for a memory kill.
* `scripts/harden-memory.sh` (needs `sudo`, `--dry-run` by default): 4 GiB free-memory headroom, automatic reboot after a hung task,
  hardware watchdog.
* **The director now also decides when to offer your documents** (search_knowledge, read_document, describe_data, query_data):
  only if the message is about them, there are attachments, or you are in a project. A question like «¿para qué sirve el programa
  hermes?» is answered directly instead of searching your files. «Programa» alone no longer counts as a request to run code. A chosen
  agent keeps its tools.

## 0.19.0 — 2026-11-05
The assistant remembers you, and remote databases are queried where they live.

* **Memory that learns** (`services/memory.py`): after each answer the assistant notes lasting facts about the person (work,
  tools, projects, how they like answers), merges repeats, raises the importance of what keeps coming up and corrects what
  changes. Credentials and identity/payment numbers are never stored; what the person wrote or imported is never rewritten or
  forgotten by the assistant. Off switch: «Aprender automáticamente» (Memory page, Settings → Privacy, preference `memory_auto`).
  Needs a resident model; it never loads one just to take notes. Migration `e6a0c3d92f55` (category, importance, mentions).
* **Memory is portable**: `GET /api/v1/memories/export?format=markdown|json` (a document to paste into another AI, or the
  structured version) and `POST /api/v1/memories/import` (our JSON or any list of «- » lines). Buttons in Settings → Privacy
  and on the Memory page.
* **Remote databases, queried live** (`services/remote_db.py`, `/api/v1/databases`, agent tools `db_schema`/`db_query`, MCP tools
  `list_databases`/`db_schema`/`db_query`): PostgreSQL through a read-only account; each call runs one statement on the REMOTE
  server and returns at most `row_limit` rows (a LIMIT is wrapped around it). Nothing is downloaded, synchronised or indexed.
  Read-only is enforced twice (a strict SQL guard and a read-only server session with timeouts); private networks need the
  administrator's allowlist; passwords are encrypted and never returned. Migration `f7b1d4a93c62`.
* **A database can no longer be copied by accident**: knowledge connectors skip `.sqlite/.db/.mdb/.dump/.bak` and `.sql` over
  2 MB (and say so), and uploads no longer text-index huge dumps or big CSV/JSON files (the data tools still query them).
* Fix: with the effort bar on «Instant», an explicit request such as «busca…» now searches the web (0.18.x).

## 0.18.0 — 2026-11-05
Faster answers, files one click away, and a first-run setup.

* **Effort bar + director.** A slider (Instant · Fast · Balanced · Deep · Maximum) sets how much the assistant thinks, and a
  *director* decides, before each answer, which of the allowed capabilities that message needs (thinking, web, deep research,
  skills, agents, code). Simple questions no longer wait for a web search or long reasoning. Cheap rules decide when the message
  is clear; the model is asked only when it is not, and never at the two fastest levels. If anything fails, the answer runs with
  everything that was allowed, as before. Works in chat, projects and the workspace (whose commands are never taken away).
* **Permissions, not obligations.** Think / Web / Research have three states: *auto* (may use it), *always*, *off*. Admin setting
  `director_enabled` turns the director off hub-wide. Each answer shows what the director chose and why.
* **Files open from the answer.** «Aquí está el informe» / «here is the report» and mentions of a file the assistant produced
  become blue links that open the canvas, the file viewer or the workspace file.
* **First-run setup** (`POST /api/v1/admin/setup`, shown once to the first administrator): default language and, optionally,
  the organisation's name (also editable in Settings → System). Nothing about any organisation is built in. Every person still
  chooses their own language, and it follows their account to any browser. The language of the assistant and of e-mails defaults
  to the hub's default language.
* **English review**: menus verified; hard-coded Spanish in a dozen pages and the live tool labels now follow the language.

## 0.17.0 — 2026-11-05
Installing models is no longer all-or-nothing, and the hub opens on the chat.

* **Custom-code warning fixed.** Only `auto_map` or `modeling_*.py`-style files count as code that `trust_remote_code` would run.
  A loose script such as Qwen3-Coder's `qwen3coder_tool_parser.py` is now an information note and needs no approval.
* **Install recipes** (`services/installers.py`): vLLM, Ollama, ComfyUI, ML worker and a new **Solo archivos** recipe that downloads
  and registers whatever you choose without loading it into any engine. It is also the automatic fallback when no engine takes the
  repository, instead of «no engine adapter».
* **File choice**: `include_patterns` / `exclude_patterns` in the install dialog (Advanced options).
* **Split GGUF** is detected up front (Ollama cannot pull it from Hugging Face): another quantisation is chosen when there is one,
  otherwise the plan says so before anything is downloaded and offers «Solo archivos».
* **Install anyway** (`force`, administrators and managers): goes past «unsupported» verdicts. Disk space, missing files and
  Hugging Face access are never skipped.
* **The hub opens on the chat.** The dashboard moved to `/dashboard` and stays first in the menu.

## 0.16.0 — 2026-11-04
Content access on the institution's rules, without asking each person to accept.

* **Institutional mode** (`POST /api/v1/admin/content-access-mode`): for when the institution's own rules already authorise
  administrators to consult content. Nobody is asked to accept anything and the reason is optional. The default stays `consent`.
* Switching to it tells every active person once, by notification (and e-mail if SMTP is configured), and the terms text changes to say so.
* Every consultation is still logged and each person still sees it in Settings → Privacy, with its reason if there is one.
  That is not a setting.

## 0.15.0 — 2026-11-04
Administrators can consult a person's content, openly.

* **New terms**: they say an administrator can consult chats, files and workspaces. Everyone is asked to accept them again;
  until they do, nobody can consult their content, because earlier acceptances were of terms that said the opposite.
* **Content in the Users panel**: only with that acceptance, with a written reason each time. Files are always downloaded, never
  opened in the browser.
* **Settings → Privacy** shows each person who consulted their content, when, what and why. The access is logged before
  anything is returned. What is sent through the API is not stored, so it cannot be consulted.

## 0.14.0 — 2026-11-04
See who is using the hub.

* **Users panel in Monitoring** (administrators): who is online, active or inactive, what they have running, and their latest
  requests, with tokens and times. Usage metadata only: no chat or job titles, file names or prompts, as the terms promise.

## 0.13.2 — 2026-11-04
Fixes found by connecting a real robot suite.

* `applyRootMotion` was ignored, so turning root motion off had no effect.
* `GET /physics-capabilities`, which suites ask before checking physics, no longer answers 404.
* `rootMarkers` are not followed yet: the answer now says so instead of ignoring them silently.

## 0.13.1 — 2026-11-04
Robot motions from text while waiting for Llama 3.

* **Provisional text encoder**: while Meta has not approved access to Llama 3, the motion server can run on Llama 3.1 8B. It
  is marked as provisional on the Robot page and in every answer, its vectors are stored apart, and the server moves to Llama 3
  by itself once all its files are there.
* The text encoder now loads when the server starts, if it fits, so the first prompt does not wait for it.
* A model only counts as installed when all its files are there, not when the first of them lands.
* **Physics check**: the simulated tracking is now only on request and never fails a motion: with generic gains and no balance
  controller even a robot standing still fell, so it said nothing. Self-collisions now report their depth.

## 0.13.0 — 2026-11-04
The Spark as the brain of a robot control program.

* **Robot page**: the address a robot suite connects to (copy it with one click; it follows Tailscale changes), the state of
  the motion server, a test box, and the latest requests received, to diagnose a program that does not connect.
* **Motion server** (`engines/robot`): text to Unitree G1 motion with NVIDIA Kimodo and physics checks with MuJoCo, on port
  5000 of the Tailscale address only. See `docs/robot-server.md`. It starts with the machine once started from the page.
* **Library**: install and load the Kimodo models a G1 can play; the human skeleton models are listed as not compatible.
* **Memory**: models stay loaded while they fit. Llama 3, which Kimodo needs for text (about 17 GB), loads only if the memory
  is free and leaves first when it runs short. Embeddings are cached on disk, so a repeated prompt does not need it.
* Kimodo cannot use the hub's main model for text: it is trained on the vectors of Llama 3 8B, which needs access approved
  by Meta on Hugging Face. The page guides you through it.

## 0.12.0 — 2026-11-04
A document viewer everywhere: open files instead of having to download them.

* **Document viewer** in the chat, projects, My files and the Workspace: images (with zoom), video and audio (seeking
  works), PDF, Word, PowerPoint and OpenDocument, Excel and CSV (all sheets), Markdown, HTML and code, with previous/next,
  download and open in another tab. PDFs are drawn with pdf.js, so they also work on phones.
* **Office files** are converted to PDF inside the code sandbox (no network, no privileges), never on the server itself;
  the result is cached by document content for 30 days. New API: `GET /api/v1/files/{id}/preview.pdf`, `/table`, `/plain`
  and `GET /api/v1/workspaces/{id}/preview.pdf`. HTML runs in an iframe that cannot reach the hub or your session.
* **Phones**: My files, Connect tools and the dashboard were wider than the screen (up to 1023 px), which shrank the whole
  page; wide content now scrolls inside its own box.

## 0.11.1 — 2026-11-04
Long tasks no longer seem stuck, and you can read while the assistant works.

* **Scrolling**: every token pulled the view back to the end, so you could not scroll up while the model worked (chat,
  projects and Workspace). It now follows only while you are at the end; moving up stops it and "Go to the end" resumes it.
* **`run_code` keeps its files in a conversation**: each run started with an empty `/work`, so in chats and projects the
  model wrote a long document, lost it in the next step and wrote it again, for minutes each time. `/work` now
  carries over between runs of the same conversation (2 GB at most; removed with the conversation or after 30 days
  without use; symbolic links are never followed).
* **Progress while a tool call is being written**: a long file sent as a tool argument takes minutes and nothing was
  shown. The live panel now says "Preparing: Running code · 12.4 k characters".

## 0.11.0 — 2026-11-03
A calmer chat, a smooth spinner, Codex and an easier Claude Code.

* **Chat layout**: the conversation gets the width (≈890 px instead of ≈540 on a 1440 px screen). Chat options
  (instructions, temperature, length, saved prompts, shortcuts) move to a panel opened with a button; the header is one
  row; only your messages have a bubble and answers read in a centred column; details under an answer appear on hover;
  the composer is one box with compact option chips. No feature was removed.
* **"How does it work?" guides** start minimised everywhere and open only when clicked.
* **Loading spinner**: with the system's "reduce motion" on (also common over remote desktops) it turned 100,000 times
  too fast, which looked like shaking; it now turns slowly. Spinners turn in step on their exact centre, and fewer run
  at once.
* **ChatGPT Codex**: new `POST /v1/responses` (OpenAI Responses API, with the hub's keys, quotas and usage) and a guide
  in Connect tools.
* **Claude Code**: `/model` lists the Spark's models (`GET /v1/models` adds `claude-spark-<alias>` for Claude Code, whose
  picker only shows ids containing "claude"), with context and state in the description. The guide creates a separate
  `claude-spark` command so your normal Claude account stays untouched. Fixed `System message must be at the beginning`:
  system messages sent mid-conversation are gathered first, for every client.

## 0.10.2 — 2026-11-03
Publishes the 0.10.1 changes below: the v0.10.1 tag was created by mistake on the 0.10.0 code. No other changes.

## 0.10.1 — 2026-11-03
Follows Tailscale changes by itself; OpenClaw and Hermes in Connect tools.

* **Automatic Tailscale sync** (`scripts/tailnet-sync.py`, a timer installed by `sah.sh tailnet-install`): when the
  account, tailnet name or IP changes, the hub's address (read live, no restart), `.env`, Tailscale Serve, Sunshine's
  `bind_address` and the Android app follow, and managers are notified. Custom addresses are left alone.
  `scripts/tailnet-root-setup.sh` (once, with sudo) keeps the permission Serve needs across account changes.
* **Hub address card** (Settings → Access and mail): address, account, IP, whether it is reachable, and step-by-step
  fixes for what needs a person (enable HTTPS on the tailnet, operator permission, disable key expiry).
* **Connect tools**: new **OpenClaw** and **Hermes Agent** guides; the API key is now written directly in everything you
  copy (Aider, Python and LangChain used environment variables).

## 0.10.0 — 2026-11-02
Tasks that run until they are done, a live activity panel, messages added to a running task, reliable web reading.

* **Runs until done**: the fixed step caps are gone (the Workspace stopped at 30 steps — all recent tasks with the new
  model ended there — and the chat at 10). The assistant plans with a new `update_plan` tool, keeps the plan updated
  and is nudged to continue while items are pending, files were written but not run, or it only announced work.
  Safety net: `agent_max_steps_*` / `agent_max_minutes_*` settings (Workspace 400 steps / 4 h, chat 40 / 30 min);
  at the limit it summarises what is done and what is left.
* **"Think" no longer leaves answers half-done**: reasoning and answer shared `max_tokens`, so a long reasoning ended
  with `finish=length` and no text, which was "continued" without its reasoning. Thinking steps now get ≥16k tokens;
  a reasoning-only cut retries with a bigger budget (then without thinking).
* **Automatic context compression inside a task**: when a long task approaches 70 % of the window, older steps are
  replaced by a model-written summary (files, results, open problems) and the plan; "context length exceeded"
  errors compress and retry instead of failing.
* **Add to a running task**: sending a message while the assistant works no longer cancels or is refused: it is
  queued and injected into the running task at the next step (`202 {queued:true}`); if the task ends first, a
  follow-up answer starts by itself.
* **Live activity panel** (chat and Workspace): plan checklist with progress, streaming reasoning, a timeline of every
  step (icon, arguments, live timer, ✓/✗, duration, terminal output, changed files, sub-agent reports, sources),
  context-usage bar and compression notices; collapses into a one-line summary and is rebuilt after a reload.
* **Web reading**: useful text from 54 typical research URLs went from 39 % to ~79 %: browser and honest-bot header
  profiles (Wikipedia blocks fake browsers), cookies, retries with backoff and `Retry-After`, anti-bot page detection,
  scholarly fallbacks (Europe PMC / OpenAlex / Crossref by DOI, PMID, PII) and the Internet Archive, with the source
  noted; fixed extraction of front pages with many `<article>` elements, header charsets and concurrent PDFs. SearXNG:
  news search works again (Bing News, DuckDuckGo News, Wikinews), Bing added to web, Europe PMC to science; its
  settings are regenerated from the template (the installed file had lost its `server` section).
* **MCP**: `GET /mcp` opens an authenticated SSE stream instead of answering 405 (OpenCode showed it as an error).
* **Skills and agents are shared**: new skills and custom agents are public by default and existing ones were made
  public; everyone can use them, only the owner or an admin/manager edits them ("Compartida por …").
* **Connect → OpenCode**: pick the model, `small_model` is always the same model (no second light model slowing the
  main one), and the API key is written into the file (create one in place); per-OS instructions.

## 0.9.1 — 2026-11-02
Sub-agents that work in the Workspace, and a catalogue of built-in agents and skills.

* **Multi-agent Workspace**: sub-agents can now list, read, write and run files of the workspace (`ws_*` tools).
  `delegate_task` takes `write_paths` (files/folders the agent may change): agents running in parallel must get
  non-overlapping paths (a second agent on the same area is refused), writes outside the area are rejected, and
  agents wait for a free run slot instead of failing. Coding agents get more steps (24) and longer outputs. The
  workspace assistant is told to plan, split separable work between parallel agents, then integrate and test; the
  files each agent changed appear as links and its report can be opened in the chat.
* **15 new built-in agents**: programador, tester, depurador, revisor-codigo, arquitecto, redactor-cientifico,
  revisor-bibliografia, revisor-pares, estadistico, visualizador, limpiador-datos, maquetador-latex, traductor,
  verificador and docente (several preload their skill).
* **17 built-in skills** shipped with the hub and installed at start-up (public, "Built-in" badge, can be disabled
  and restored): informe-latex, presentacion-beamer, citas-bibtex, excel-con-graficos, documento-word,
  presentacion-pptx, graficos-publicacion, analisis-estadistico, limpieza-datos-pandas, informe-desde-datos,
  articulo-cientifico-imrad, revision-por-pares, propuesta-investigacion, tfg-tfm, docencia-rubricas,
  traduccion-tecnica and python-tests. Every code and LaTeX template was run in the sandbox image.
* **Sandbox image**: adds `pytest`, the journal LaTeX classes (`texlive-publishers`: IEEEtran, elsarticle, llncs,
  acmart…) and the Carlito/Caladea fonts (metric-compatible with Calibri/Cambria).

## 0.9.0 — 2026-11-02
New main model, a simple way to choose it, message actions in the chat and a redesigned Workspace.

* **Qwen3.8-Flash-Next FP4 as the general model** (`nvidia/Qwen3.8-Flash-Next-FP4-Local`: 125B MoE, 6B active,
  vision and tools, NVIDIA's quantization-aware FP4 for local hardware). It needs a community DGX Spark vLLM build
  with the b12x kernels: the engine manager now has fixed **launch profiles** (image pinned by digest, environment and
  flags allow-listed; nothing comes from the caller). The 51B-parameter n-gram table is read from disk with io_uring,
  allowed by a seccomp profile that is Docker's default plus the three io_uring calls (not `seccomp=unconfined`).
  Measured on the Spark: 70.3 GiB resident, FP8 KV cache for 625k tokens, 256k context, MTP (4 draft tokens),
  ~45 tok/s. The first start autotunes the kernels (~45 min, cached afterwards in `cache/vllm/b12x`).
* **Longer windows with YaRN**: Qwen models can go beyond their native window (up to 4×, 1M at most); the context card
  marks those sizes "YaRN" and explains the trade-off (better for long texts, slightly worse on short chats).
* **Main model** card on the Models page: current model, state, memory and context; pick another one among the
  downloaded models, the recommended ones or any Hugging Face repository ("Download and make main" switches by itself
  when the download ends, with progress, speed and ETA). New API: `GET/POST /api/v1/models/main`,
  `GET /api/v1/models/main/plan`, `DELETE /api/v1/models/main/pending`.
* **Load / Unload buttons** on every model (library and detail page) with live state, ETA and memory, and the reason
  when an action is not possible.
* Model loads: a profile can allow a long first start (2 h); a load in progress survives a worker restart (the hub
  waits for the running container instead of restarting it).
* **Chat message actions**: copy any message, copy code blocks, edit a sent message and regenerate, regenerate the
  last answer, reply quoting a selected piece of an answer, and "resume from here" (deletes what follows). Same actions
  in the Workspace assistant. New API: `POST …/messages/{id}/regenerate`, `POST …/messages/{id}/truncate`; the
  context summary is invalidated when it covered removed messages. Your own messages are selectable again (the
  selection colour was the bubble colour).
* **Workspace redesigned**: workspace switcher (rename, sizes, last used), resizable and collapsible panels, file
  tree with icons, context menu, drag & drop to folders and move, editor tabs with unsaved marks and Ctrl+S, conflict
  warning when a file changes on the server, and a **multi-line terminal** (Enter runs, Shift+Enter new line, Tab
  indents, history) with run history, exit codes, duration and a **Stop** button for background runs. Mobile: panels
  become tabs.

## 0.8.1 — 2026-11-01
Connectors verified against real servers; voice fixes.

* **WebDAV connector** now reads every `propstat` block of a PROPFIND answer (servers such as WsgiDAV and Nextcloud
  put found and missing properties in separate blocks): sub-folders were not followed and some files had no ETag.
  Verified against a real WebDAV server: new files, sub-folders, changed files and deleted files.
* **SMB connector**: each connector uses its own connection cache (the shared pool reused another connector's session
  and credentials for the same server: "access denied"); guest shares work (NTLM without signing); clear messages for
  wrong passwords, missing shares and folders. Verified against Samba: guest, user/password and wrong password.
* The sync statistics count deletions correctly.
* **ML worker**: `transformers` is imported at start-up; two simultaneous requests (e.g. a smoke test and a
  read-aloud) could fail with "cannot import name AutoTokenizer".
* English read-aloud: install `facebook/mms-tts-eng` (the chat picks the voice matching the user's language).
* Admin PDF guide: background replies, workspace packages and LaTeX, backups of users' files.

## 0.8.0 — 2026-11-01
Deep review: fixes and hardening across the platform.

* **Backups were missing user files**: "include user data" archived datasets, outputs and experiments but not the
  users' drives (`users/`, since 0.2.0) nor workspaces. Both are included now (without `node_modules`/installed
  packages); symbolic links and special files are skipped. Verified with a real backup.
* **Answers cut by the length limit** (`finish_reason=length`) now continue automatically (twice at most) instead of
  ending mid-sentence; the chat's default output limit is 8192 tokens.
* **Small models** (< 14B, e.g. Qwen3 4B) get only the core tools and a shorter prompt: with 20+ tools they stopped
  calling the right one (memory regression found by the acceptance tests). «Recuerda que…» is always saved, even if
  the model forgets to call the tool.
* **Workspaces**: deleting a workspace stops its running reply and deletes its chat (a running reply could recreate a
  deleted workspace); workspace chats no longer clutter the Chat sidebar; uploads are streamed to disk instead of read
  into memory; change detection skips `node_modules`/`.local` (fast after `npm install`).
* **Fair sandbox use**: at most 2 programs per person at once (chat + workspaces, setting `code_exec_per_user`); the
  slot frees itself if a process dies. The engine manager removes sandbox containers left by a restart.
* Replies longer than 2 h keep their state alive through the heartbeat.
* **Version**: the app reports its real release (`/api/v1/system/health`, MCP, backups) instead of the Docker image tag.
* `scripts/sah.sh up|update` create the runtime folders before Compose mounts them and `update` also rebuilds the
  sandbox image.
* Tests: acceptance tests can run with an existing researcher account (`SAH_TEST_USER`, `SAH_TEST_USER_PASSWORD`),
  no admin password needed; 27 researcher-level acceptance tests and 36 unit tests pass.

## 0.7.0 — 2026-11-01
Reliable background work, LaTeX and package installs in workspaces, skills in workspaces.

* **No more "zombie" chats**: every running reply keeps a heartbeat in Redis while its thread lives; a reply whose
  process died (backend restart, crash) is closed as interrupted with a message, instead of showing "working" for two
  hours. Deleting a conversation stops its reply.
* **"N chats working" menu**: lists every running reply with its title, elapsed time and current step, opens it (in the
  Chat or in its Workspace) and has a **Stop** button for each.
* **LaTeX in workspaces**: the sandbox image now includes TeX Live (pdflatex, xelatex, lualatex, latexmk, biber, most
  LaTeX packages, Spanish and English).
* **Install missing packages**: in workspaces `pip install` / `npm install` work and persist in the workspace
  (`.local`, `node_modules`). The sandbox still has no network interface: a loopback forwarder talks to the new
  `pkgproxy` service through a Unix socket, and the proxy only allows PyPI and npm over HTTPS (admin switch).
* **Skills in workspaces**: loading a skill copies its files to `.skills/<name>/` so its scripts can run with `ws_run`.
* Workspace assistant: long files are written in parts (`ws_append`); a tool call cut by the output limit is reported
  to the model instead of running with empty arguments; images generated in a workspace chat are saved in `imagenes/`
  so documents can include them; up to 16k output tokens per step.
* The Workspace opens the right workspace from links (`/workspace?w=<id>`).

## 0.6.1 — 2026-11-01
Work keeps going — and stays visible — when you leave the page.

* Workspace assistant: coming back to the Workspace (another page, window or a reload) re-attaches to a reply in
  progress and replays its steps live, as the Chat already did.
* Workspace terminal: commands run in the background on the server (`POST /run` with `background: true`); the last 30
  runs per workspace are kept for 7 days (`GET/DELETE /runs`), so any window shows what is running and how it ended.
  Up to 3 commands at once per workspace; a button clears the terminal.
* Audio Studio lists recent transcription/speech jobs from the server instead of the page memory.

## 0.6.0 — 2026-11-01
Workspace: a remote harness per user.

* **Workspace** page: persistent folders per user (several per person, ZIP download, 10 GB each by default) with a
  file tree (upload, drag & drop, import from My files, new file/folder, rename, delete), an editor (Ctrl+S, ▶ to run
  `.py`/`.sh`/`.js`), previews (Excel/CSV as tables with sheets, PDF, images, HTML in a sandboxed frame, Markdown) and a
  terminal. Every command runs in the no-network sandbox with the workspace mounted as `/work` (engine-manager
  `workspace` mount; the program itself comes read-only from `/job`); up to 10 min per command by default.
* **Workspace assistant**: its own chat per workspace with `ws_list`, `ws_read`, `ws_write`, `ws_edit` and `ws_run`; it
  writes code, runs it, reads the errors and fixes them (up to 30 tool steps) and leaves Excel, PDF, charts or scripts in
  the workspace. Verified: CSV → Excel with detail and monthly sheets + chart + one-page PDF report.
* Untrusted files written by programs are handled safely: symbolic links are never followed from the web, only regular
  files are read (no FIFOs/devices), paths cannot leave the workspace.
* Settings: `workspace_timeout_s`, `workspace_max_gb`. Migration 38ed2f96b523.
* OpenCode configuration from "Connect tools" adds `headerTimeout` (30 min) so the first request to a model that is not
  in memory waits for it to load, labels each model with its context and marks the general one.
* Chat: the "Think" switch is remembered (per browser and per conversation).

## 0.5.0 — 2026-11-01
Code, canvas, deep research, voice, scheduled tasks, connectors, single sign-on and a usage panel.

* **Faster answers (MTP)**: vLLM speculative decoding with the model's own multi-token-prediction layers
  (`speculative_tokens`, switch on the model page; set automatically when installing Qwen3.5/Qwen3-Next). General model:
  50 → 63 tok/s for one user, 117 → 129 tok/s for four (2 draft tokens; 3 gave no further gain on GB10).
* **Code execution**: `run_code` tool (Python, bash, Node) in a fresh sandbox container per run — no network,
  read-only root, uid 1000, no capabilities, memory/CPU/pids/time limits (engine-manager `/sandbox/run`, image
  `engines/sandbox`: numpy/pandas/scipy/scikit-learn/matplotlib, python-docx/pptx/openpyxl/reportlab, Node docx and
  pptxgenjs, pandoc, LibreOffice). Files written to `/work` go to My files → "Resultados de código" and show in the chat
  (images inline). Skills can now run their scripts (`run_code(skills=[…])`). Admin switch and limits.
* **Canvas**: `canvas_write` / `canvas_edit` open a document or code file beside the chat; the user edits it, restores
  versions (last 30), asks for changes and exports to Word, PDF, Markdown, HTML or the source file (pandoc +
  LibreOffice in the sandbox), or saves a copy to My files.
* **Deep research**: "Deep research" switch (or `deep_research` tool): plan → 3–7 researcher agents in parallel (plus
  the user's documents on request) → long report with numbered sources in the canvas. Verified: 7 agents, 29 sources,
  ~6 min.
* **Voice**: dictation (🎤, local Whisper) and read-aloud ("Listen", "Read answers"; local MMS-TTS voices per language)
  through the ML worker; audio never leaves the Spark. Android app already grants the microphone.
* **Scheduled tasks**: Tasks page and `schedule_task` tool ("every Monday at 9 …"): hourly/daily (weekdays)/weekly/
  monthly in the user's time zone, web/deep-research options, own chat per task, bell notification and optional e-mail
  with the answer, "Run now". Runs from the worker (2 at a time); tokens counted as source `task`.
* **Knowledge connectors**: Nextcloud/ownCloud/WebDAV, GitHub (public or token), Zotero (user/group libraries) and SMB
  network shares, synced hourly/daily/manually into a collection (new, changed and deleted files). Remote addresses must
  be public unless an admin lists the private host/network (`connector_private_hosts`); loopback, link-local and the
  hub's containers are always refused; connections pinned to the checked IP; secrets encrypted.
* **Sign in with Google, Microsoft or any OpenID Connect provider**: PKCE, state bound to the browser, nonce,
  iss/aud/exp checks; accounts linked by e-mail only when the provider vouches for it; new accounts follow the sign-up
  mode (approval/open/closed) and allowed domains. Step-by-step setup with the redirect address in Settings → Access.
* **Usage panel**: tokens per day, per model, per source (chat/API/tasks) and per project, GPU/processing jobs, storage
  and monthly quota; managers see everyone (numbers only).
* Database migration 7982a336c53a (canvas_docs, scheduled_tasks, kb_connectors, connector_items, user_identities).
* New dependencies: smbprotocol 1.17.0, pyspnego 0.12.4 (backend); sandbox image built by `install.sh` /
  `scripts/sah.sh build`.

## 0.4.0 — 2026-11-01
Bigger context for coding agents, one-command install and first public release.

* Context window per model: new "Context window" card on a vLLM model's page with presets (8k … the model's trained
  maximum, read from `config.json`). Each preset shows how many conversations of that size fit at once, computed from
  the KV cache vLLM actually reserved (`/metrics` `kv_cache_size_tokens`). "Apply" stores `max_model_len` and reloads
  the model in the background. API: `GET/POST /api/v1/models/{id}/context`.
* The recommended general model (Qwen3.5-35B-A3B-FP8) now serves **256k tokens** with the same memory as before (hybrid
  attention: only 1 in 4 layers keeps a KV cache); verified with a 167k-token prompt. OpenCode, Cline and Claude Code
  configurations in "Connect tools" pick the new window up automatically; OpenCode steps explain `/compact`.
* Installing a model from Hugging Face sets vLLM tool-calling and reasoning parsers for known families (Qwen3.5 /
  Qwen3-Coder → `qwen3_coder`, Qwen3/Qwen2.5 → `hermes`, Llama 3 → `llama3_json`, Mistral → `mistral`, gpt-oss →
  `openai`), so tools work right after install.
* `install.sh`: requirement checks (architecture, Docker, NVIDIA toolkit, memory, disk), `.env` generation (address from
  Tailscale), host preparation, secrets, image build and start-up, printed next steps. Tailscale is optional.
* Instance-neutral code: the address comes from `.env` / the browser, the organisation name is a setting
  (`SAH_ORG_NAME`, used in the terms, e-mails and the assistant prompt), the Android app gets its default address at
  build time, no hard-coded hostnames, paths or e-mail servers.
* README with screenshots, Spanish README, MIT licence.

## 0.3.0 — 2026-10-30
Web search, skills, sub-agents and context management in the web chat.

* Web search: private SearXNG container (`searxng`, egress network only, no published port) with engines tested from
  a university network (Google CSE, Yahoo, Yandex, Wikipedia; arXiv/Scholar/Semantic Scholar/OpenAlex/Crossref/PubMed for
  science; GitHub/Stack Overflow/MDN for code). Chat tools `web_search` and `fetch_url` (HTML → readable text, PDFs via
  pypdfium2). Fetching is SSRF-safe: every hop is resolved and must be a global address (no private, tailnet, Docker,
  loopback or link-local), ports 80/443/8080/8443 only, connection pinned to the checked IP, 6 MB limit. Per-chat
  "Web" switch; admin switch.
* Skills (Agent Skills layout, SKILL.md + text files): catalog from GitHub sources (default `anthropics/skills`),
  install from owner/repo/path, GitHub URL or SKILL.md link, write your own, enable/disable, update, publish for everyone
  (admin/manager). The chat lists installed skills and loads them with `use_skill`/`read_skill_file`; it can find
  (`find_skills`) and install (`install_skill`) skills by itself when the admin allows it. Skills are text only: scripts
  are never executed.
* Sub-agents: `delegate_task` runs built-in (investigador, documentalista, analista, revisor, general) or custom agents
  with their own context and tool subset; several calls in one step run in parallel (admin limit, default 3). Live steps
  and the agent report in the chat; Agents page to create/edit custom agents.
* Context window: per-chat window size (4k … model maximum), live meter in the chat header, automatic compaction (the
  model summarises older messages when the history exceeds half of the free window; summary stored on the conversation,
  messages kept), "Compact now", view/forget summary. Requests are fitted to the window (old tool results trimmed,
  max_tokens clamped, chars/token calibrated from the engine's usage, one stricter retry on context-length errors).
* Tool loop: up to 10 steps; tool results of the current turn feed the window estimate.

## 0.2.1 — 2026-10-30
Memory policy and background chats.

* General model always loaded for everyone: `qwen3.5-35b-a3b-fp8` (vision, tools `qwen3_coder`, 64k context,
  8 concurrent sequences, ≈ 53 GB reserved; 50.8 tok/s single stream, 109 tok/s with 4 users). Pinned, preferred
  resident, default for new chats and for Claude Code / OpenCode; the worker reloads it automatically if it is missing.
* vLLM runs one container per model (`sah-vllm-<alias>`): the general model and extra vLLM models can coexist;
  start-ups are serialised. Extra models unload after 5 idle minutes (hybrid mode).
* Memory panel (dashboard, library, monitoring and a bar in the header): reservations by kind, loading progress with
  ETA from the last load, idle countdown to auto-unload, running image/video jobs, unload buttons. Loading a model shows
  a plan first (memory needed, idle models that would be unloaded, ETA) — in the library and when sending a chat message.
* KV-cache estimate counts only full-attention layers for hybrid models (Qwen3.5/Qwen3-Next).
* Chat answers run in the background: leaving the page no longer stops them. Events are kept in Redis; reopening the
  chat replays and follows them live; header indicator "N chats working", completion toast, browser notification when
  the tab is hidden and a bell notice for answers longer than 30 s. Stop really cancels; a second message while one is
  running is refused.

## 0.2.0 — 2026-10-30
Personal private cloud, harness access and Android app.

* Accounts: self sign-up with approval (any domain by default, optional allow-list), e-mail verification, password
  recovery, terms of use with versioning, **manager** role (users, sign-ups, model requests; no system settings),
  invitation e-mails with Tailscale steps (SMTP outbox with retries), optional Tailscale API
  device invites, in-app notifications (bell) with e-mail copies, data export (ZIP) and account deletion.
* Personal cloud: My files (typed uploads, magic-byte checks, text extraction for PDF/Word/PowerPoint/Excel/HTML/code),
  knowledge base (pgvector + full-text hybrid search, Qwen3-Embedding-0.6B), projects (instructions, documents, chats,
  read/write sharing), memory and personal instructions, full-text search over chats.
* Chat agent: attachments (images to vision models, documents inline or via read_document, scanned PDFs as images,
  data files), tools generate_image / edit_image (Qwen-Image + Qwen-Image-Edit-2509 with Lightning LoRAs),
  search_knowledge, describe_data / query_data (DuckDB/SQLite sandboxed subprocess), save_memory; image mode; thinking
  switch; streamed tool progress.
* Harness: Anthropic Messages API (/v1/messages, streaming, tools; Claude Code), MCP server (/mcp, streamable HTTP,
  12 tools), "Connect tools" page generating per-tool/per-OS setup with the user's key (OpenCode, Claude Code, Continue,
  Cline, Cursor, Claude Desktop, Aider, Python, LangChain, Open WebUI, curl).
* In-app guides on every page, help centre, onboarding; mobile layout (bottom tab bar by role, chat drawer).
* Android app (es.upv.idf.sparkhub 0.2.0): WebView shell with Tailscale detection/launch, camera and file pickers,
  share-to-chat, downloads; built on the Spark without Gradle (android/build.sh); served at /api/v1/app/android.
* PDF guides for users and administrators with real screenshots (docs/guides/).
* Database: postgres image → pgvector/pgvector:0.8.7-pg16-trixie (PostgreSQL 16.15); migration cda914892726.

### Coding agents (OpenCode) over the gateway

* Fix: Ollama truncated prompts longer than ~4k tokens (8192 context shared by the request) — now
  `OLLAMA_CONTEXT_LENGTH=32768` with flash attention and q8_0 KV cache (Qwen3 4B: 13.5 GB resident).
* Fix: `tools` on vLLM models is accepted only when the instance was started with a tool-call parser (was gated on
  the HF capability list, which never included `tools`).
* Fix: Ollama reservations follow the size reported by `/api/ps` (periodic `sync_ollama`), and a post-load
  measurement can raise a reservation as well as lower it.
* Models: Qwen3.5-35B-A3B (BF16, vLLM, 64k context, `qwen3_coder` tools, ≈90 GB, 30 tok/s); Qwen3-8B-FP8 now 32k context
  with `hermes` tools. Verified tool calls with a 12.5k-token prompt on all three chat models.
* Docs: OpenCode provider configuration (API.md).

## 0.1.0 — 2026-10-29
Initial deployment on a DGX Spark (DGX OS 7.5.0, driver 580.142, CUDA 13.0).

* Core: FastAPI backend (modular monolith), PostgreSQL 16, Redis 8, Next.js 15 frontend, Caddy reverse proxy,
  Tailscale Serve (tailnet-only HTTPS). Cookie sessions + CSRF, Argon2id, RBAC (admin/researcher/viewer), invitations.
* Engines: Ollama 0.34.4 (always on), vLLM (NGC 26.09, on demand), ComfyUI v0.42.5 and ML worker on NGC PyTorch 26.09
  (on demand), all managed by an allow-listed engine manager.
* Hugging Face explorer, deterministic compatibility/recommendation engine, download manager with sha256 verification,
  model registry and lifecycle, conservative unified-memory scheduler (hybrid/on-demand/persistent).
* OpenAI-compatible gateway (/v1 models, chat completions with streaming, embeddings), per-user API keys, quotas,
  rate limits, privacy-aware usage logging.
* Playground, model comparison, benchmarks (perf + deterministic quality suites), Image/Video/Audio studios,
  Scientific Lab (datasets, trusted adapters, experiments), jobs with recovery, monitoring + alerts, backups + restore test.
* Fixes found by acceptance testing: periodic job reconciliation after fast worker restarts, HF download retries with
  partial-snapshot detection, benchmark environment GPU identity, webp MIME type, grid overflow, a11y heading.
