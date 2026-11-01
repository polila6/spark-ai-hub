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
* Cargar/descargar manualmente: **Biblioteca** → Cargar / Descargar de memoria.

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

## 10. Si algo falla

docs/TROUBLESHOOTING.md · `scripts/sah.sh logs <servicio>` · Monitorización → Contenedores → logs.
