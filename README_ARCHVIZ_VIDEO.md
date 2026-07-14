# ARCHVIZ Video - Guía rápida para videos largos en GPU

## Objetivo
Generar videos largos de escenas arquitectónicas usando ComfyUI local con tu GTX 1070 Ti.

## Configuración actual
- GPU: NVIDIA GeForce GTX 1070 Ti 8GB VRAM
- Modelo base actual: `v1-5-pruned-emaonly.safetensors` (SD 1.5, más liviano)
- Backend: ComfyUI local en `archviz/comfyui`
- Modo seguro confirmado en endpoints y monitoreo GPU

## Arranque recomendado
Usa los scripts con flags de bajo consumo:
- Windows: `archviz/comfyui/launch_lowvram.bat` o `launch_lowvram.ps1`
- Flags aplicados: `--lowvram --fp16`

## Workflow actual
- Renders estáticos por frame (`/api/archviz/process`) encolados en ComfyUI.
- Storyboard y generación de video encolan set de frames para componer luego.
- Modelo actual es imagen estática; para videos largos reales se requiere AnimateDiff / RIFE.

## Próximos pasos para videos largos
1. Instalar custom nodes de video (AnimateDiff / RIFE) en `archviz/comfyui/custom_nodes`.
2. Cambiar workflow de imagen a video en los endpoints.
3. Ajustar lote de frames y resolución por VRAM.

## Nota importante
Mientras animes motion real, prioriza resoluciones 512x512, steps <= 20 y CFG moderado para estabilidad en 8GB VRAM.