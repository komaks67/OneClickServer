@echo off
cd /d "%~dp0"
echo ==== OneClickServer : build exe ====
echo.
set PY=
where py >nul 2>nul
if errorlevel 1 goto trypython
py -3.13 --version >nul 2>nul
if not errorlevel 1 set PY=py -3.13
if defined PY goto havepy
py -3.12 --version >nul 2>nul
if not errorlevel 1 set PY=py -3.12
if defined PY goto havepy
py -3.11 --version >nul 2>nul
if not errorlevel 1 set PY=py -3.11
if defined PY goto havepy
echo [Chuui] Python 3.13 / 3.12 / 3.11 ga mitukarimasen. python.org kara Python 3.13 wo install shite kudasai.
set PY=py -3
goto havepy
:trypython
where python >nul 2>nul
if not errorlevel 1 set PY=python
:havepy
if not defined PY goto nopy
echo Using: %PY%
%PY% --version
echo.
echo [1/3] pyinstaller / pywebview wo install shimasu ...
%PY% -m pip install --upgrade -r requirements.txt
if errorlevel 1 goto pipfail
echo.
echo [2/3] exe wo tukurimasu (suu fun kakarimasu) ...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
%PY% -m PyInstaller --noconfirm --distpath dist --workpath build OneClickServer.spec
if errorlevel 1 goto buildfail
copy /y README_exe.txt "dist\OneClickServer\README.txt" >nul
echo.
echo [3/3] OK!
if not exist "dist\OneClickServer\_internal\base_library.zip" (
  if not exist "dist\OneClickServer\_internal\encodings" (
    echo [Error] _internal ni Python no bubun ga arimasen. Python 3.13 de tukurinaosite kudasai.
    pause
    exit /b 1
  )
)
echo Done: %~dp0dist\OneClickServer\OneClickServer.exe
echo Next: run make_release.bat to make the zip for distribution.
goto end
:nopy
echo [ERROR] Python ga mitukarimasen. python.org kara install shite kudasai. ("Add python.exe to PATH" ni check)
goto end
:pipfail
echo [ERROR] pip ni shippai shimashita. Internet wo kakunin shite kudasai.
goto end
:buildfail
echo [ERROR] exe no sakusei ni shippai shimashita. Ue no akai hyouji wo oshiete kudasai.
:end
echo.
pause
