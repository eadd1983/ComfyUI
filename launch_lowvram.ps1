$ErrorActionPreference='SilentlyContinue'
Write-Host "Iniciando ComfyUI en modo ahorro de VRAM para GTX 1070 Ti..."
Set-Location $PSScriptRoot
python main.py --lowvram --fp16
Read-Host "Presiona Enter para cerrar..."