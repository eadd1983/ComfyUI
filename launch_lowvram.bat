@echo off
echo Iniciando ComfyUI en modo ahorro de VRAM para GTX 1070 Ti...
cd /d %~dp0
python main.py --lowvram --fp16
pause