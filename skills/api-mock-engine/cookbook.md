# Cookbook: API Mock Engine & Fixture Generation

## Objective
Generate a lightweight, standalone mock API server with simulated endpoints and synthetic data fixtures.

## Prerequisites
- python 3.11+
- fastapi or flask (optional for server launch)

## Execution Recipe
1. **Define Schema**:
   Identify required endpoints, methods (GET/POST), status codes, and JSON response models.
2. **Scaffold Mock Server Script**:
   Generate `mock_server.py` using standard library `http.server` or `FastAPI`:
   ```powershell
   python -c "import http.server, socketserver; print('Ready to serve mock endpoints')"
   ```
3. **Generate Synthetic Fixtures**:
   Create mock JSON data payloads under `tests/fixtures/<endpoint>.json`.
4. **Verification Gate**:
   Query the mock endpoint with a curl or python script:
   ```powershell
   python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health').read())"
   ```
5. **Remediation**:
   If port is in use, bind to dynamic port `8080` or increment port number.
