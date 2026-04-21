@echo off
echo Starting Government Contract Opportunities API Server...
echo.
echo Make sure you have installed the requirements:
echo pip install -r requirements.txt
echo.
echo Then run the API server with:
echo uvicorn fastapi_server:app --reload --host 0.0.0.0 --port 8000
echo.
echo The API will be available at http://localhost:8000
echo API documentation will be available at http://localhost:8000/docs
pause