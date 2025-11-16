@echo off
echo =======================================
echo   PUSH AUTOMATIQUE VERS GITHUB
echo =======================================
echo.

REM Aller dans le dossier du script
cd /d "%~dp0"

REM Ajouter tous les changements
git add .
echo - Fichiers ajoutés

REM Créer un commit automatique avec la date/heure
set dateTime=%date% %time%
git commit -m "Auto-commit %dateTime%"
echo - Commit créé

REM Push vers GitHub
git push
echo - Push effectué

echo.
echo =======================================
echo   PUSH TERMINE — GITHUB EST A JOUR !
echo =======================================
pause
