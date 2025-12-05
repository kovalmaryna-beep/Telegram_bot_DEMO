@echo off
echo Встановлюю Python та необхідні компоненти...
winget install --id=Python.Python.3.11 -e --silent
python -m ensurepip --upgrade
python.exe -m pip install --upgrade pip
pip install --upgrade pip
pip install playwright
pip install python-telegram-bot==20.3
playwright install
echo Установка завершена.
pause