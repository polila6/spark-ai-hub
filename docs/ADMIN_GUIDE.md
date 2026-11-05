# Guía rápida del administrador (Spark AI Hub)

## 1. Acceso

* URL: **https://your-spark.your-tailnet.ts.net** (solo desde dispositivos de tu red Tailscale).
* Usuario inicial: `admin`. La contraseña inicial está en `/srv/spark-ai-hub/config/secrets/admin_password`
  (solo legible por tu usuario). **Cámbiala al entrar**: Ajustes → Preferencias → Cambiar contraseña.

## 2. Arrancar, parar, ver estado

```bash
cd ~/spark-ai-hub
scripts/sah.sh status        # estado de servicios y motores
scripts/sah.sh up            # arrancar todo
scripts/sah.sh down          # parar todo (los datos se conservan)
scripts/sah.sh restart worker
scripts/sah.sh logs backend  # logs en vivo (Ctrl+C para salir)
```

Tras reiniciar el DGX la plataforma arranca sola (Docker + `restart: unless-stopped`).

## 3. Añadir investigadores

1. **Usuarios y permisos → Invitar usuario**: nombre de usuario, nombre, rol (`researcher` normalmente).
2. Copia el enlace de invitación (válido 72 h, un solo uso) y envíalo por un canal seguro.
3. El investigador debe estar en tu tailnet (Tailscale → invitar usuario/dispositivo) para abrir el enlace.
4. ¿Contraseña olvidada? → botón **Enlace de restablecimiento** (24 h).
5. Para retirar el acceso: **Desactivar** (revoca sesiones y claves API; sus datos se conservan).

Cuotas por rol (trabajos concurrentes, cola, GB, peticiones/min, tokens/mes, vídeo) en **Usuarios → Políticas**.

## 4. Instalar modelos

1. **Descubrir modelos** → buscar (o pegar una URL de huggingface.co) → abrir el modelo.
2. Pestaña **Compatibilidad**: veredicto (Verificado / Probablemente compatible / Experimental / No compatible /
   Información insuficiente), motivos, estimación de memoria y disco.
3. **Instalar** → **Plan de instalación** → revisar avisos (licencia, código remoto, pickle) → Instalar.
4. Progreso en **Biblioteca → Descargas** (pausar/reanudar/cancelar). Al terminar: verificación sha256, registro y
   **prueba rápida** automática con inferencia real.
5. Las solicitudes de investigadores aparecen como "Pendiente de aprobación": **Aprobar**.
6. Modelos de imagen/vídeo: **Image/Video Studio → Instalar modelos del flujo** (descarga los ficheros oficiales).
7. Repositorios *gated* (Llama, Gemma…): acepta la licencia en huggingface.co y guarda tu token en
   **Ajustes → Sistema → Hugging Face** (se guarda cifrado).

Recomendación: para vLLM, prioriza los modelos de la matriz NVIDIA para DGX Spark (FP8/NVFP4), marcados "NVIDIA Spark" en el Recomendador.

## 5. Memoria y planificador

* El DGX Spark tiene **memoria unificada** (CPU+GPU, ~121 GiB). El planificador reserva memoria por modelo cargado y
  por trabajo; si no cabe, el trabajo espera con el motivo visible.
* Modo **híbrido** (por defecto): el *modelo residente preferido* se queda cargado; el resto se descarga tras 15 min sin uso.
* Ajustes → Sistema → Planificador: modo, tiempos de inactividad, reserva del sistema (14 GB), trabajos GPU-heavy simultáneos.
* Cargar/descargar manualmente: botones **Cargar** / **Descargar** en cada modelo de la Biblioteca y en su ficha (con
  estado, tiempo estimado y memoria; si no se puede, dice por qué y qué habría que descargar).
* **Modelo principal** (tarjeta arriba en Modelos, solo administradores/gestores): elige entre los descargados, los
  recomendados o cualquier repositorio de Hugging Face. «Hacer principal» lo fija, desfija el anterior (que pasa a
  descargarse cuando haga falta sitio) y cambia el modelo por defecto del chat, la API y las herramientas. «Descargar
  y hacer principal» cambia solo cuando acaba la descarga.
* **Perfiles de arranque**: algunos modelos necesitan una compilación especial de vLLM. Qwen3.8-Flash-Next FP4
  (`nvidia/Qwen3.8-Flash-Next-FP4-Local`) usa la imagen comunitaria `eugr/spark-vllm-b12x` (fijada por digest) con los
  parámetros fijos del gestor de motores. El primer arranque **afina los kernels unos 45 min** (se guarda en
  `/srv/spark-ai-hub/cache/vllm/b12x`; los siguientes tardan ~5 min). Ocupa 70 GiB y reserva ~90 GiB con la caché de
  contexto (625k tokens).
* **Ventana de contexto**: Modelos → (modelo) → «Ventana de contexto». En los Qwen, los tamaños por encima de la
  ventana nativa activan YaRN (hasta 4×): más texto, algo menos de calidad en conversaciones cortas.

## 6. Copias de seguridad

* Ajustes → **Copias de seguridad**: programación diaria (03:00 UTC por defecto), "Crear copia ahora", "Probar restauración".
* Las copias locales están en el mismo disco: **configura un destino externo** (docs/BACKUP_RECOVERY.md) y guarda los
  ficheros de `/srv/spark-ai-hub/config/secrets` en un lugar seguro fuera del equipo.

## 7. Supervisión

* **Panel**: CPU, memoria unificada, GPU (utilización, temperatura, potencia), disco, modelos residentes, alertas.
* **Monitorización**: históricos, contenedores (con logs) y alertas (disco, memoria, temperatura, servicios caídos,
  fallos repetidos, colas largas).
* **Ajustes → Diagnóstico**: paquete ZIP con secretos redactados para soporte.

## 8. API para investigadores

Cada investigador crea su clave en **Centro API** y usa `https://your-spark.your-tailnet.ts.net/v1` con el SDK de OpenAI
(ejemplos en la propia página y en docs/API.md). Nunca compartas claves; se revocan con un clic.

## 9. Actualizar

```bash
scripts/sah.sh backup && git pull --ff-only && scripts/sah.sh build && scripts/sah.sh up
```

## 10. Funciones del chat (0.5)

* **Ejecución de código**: Ajustes → Sistema → «Chat: internet, skills, agentes y código». Cada ejecución es un
  contenedor nuevo sin red ni privilegios (imagen `spark-ai-hub/sandbox`, se construye con `scripts/sah.sh build`).
  Ajusta el tiempo (5–900 s) y la memoria por ejecución; hay como máximo 4 a la vez.
* **Voz**: usa los modelos de audio instalados (Whisper para dictar, MMS-TTS para leer). Para inglés instala
  `facebook/mms-tts-eng` en Descubrir modelos. Se activa/desactiva en «Voz y conectores».
* **Conectores**: solo llegan a direcciones públicas salvo las redes o servidores que añadas en «Redes privadas para
  conectores» (p. ej. `192.168.1.0/24` o `nas.laboratorio.local`). El propio Spark y sus contenedores nunca se permiten.
  La misma lista decide qué servidores PostgreSQL privados pueden usarse en **Conocimiento → Bases de datos**: esas
  conexiones se consultan en remoto (solo lectura, filas limitadas) y el Spark no descarga ni indexa la base de datos.
* **Entrar con Google o Microsoft**: Ajustes → Acceso y correo → «Entrar con Google o Microsoft». Cada tarjeta trae los
  pasos y la dirección de redirección que hay que copiar en la consola del proveedor. Las cuentas nuevas siguen el modo
  de registro (con aprobación, abierto o cerrado).
* **Respuestas más rápidas (MTP)** y **ventana de contexto**: Biblioteca → (modelo) → «Ventana de contexto».
* **Workspace**: cada usuario tiene carpetas persistentes en `/srv/spark-ai-hub/workspaces/<usuario>/<workspace>`.
  Los comandos usan el mismo entorno aislado sin red; límites en «Workspace: tiempo por comando» y «tamaño máximo».
  Si está activado «permitir pip install / npm install», los workspaces pueden descargar paquetes solo de PyPI y npm a
  través del servicio `pkgproxy` (socket local, sin tarjeta de red). Cada persona puede tener 2 programas a la vez.
* **Copias de seguridad**: con «incluir datos de usuario» se guardan también Mis archivos de todos (`users/`) y los
  workspaces (sin `node_modules` ni paquetes instalados, que se pueden reinstalar).
* **Uso**: página Uso → «Todo el equipo» (solo cifras, nunca contenido).

## 11. Si algo falla

docs/TROUBLESHOOTING.md · `scripts/sah.sh logs <servicio>` · Monitorización → Contenedores → logs.
