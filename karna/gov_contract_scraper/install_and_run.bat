@echo off
echo Installing Government Contract Scraper System dependencies...
pip install -r requirements.txt

echo.
echo Starting Government Contract Scraper System...
python contract_scraper.py

echo.
echo Scheduler can be started with: python scheduler.py
pause