"""Small runner that imports the backend `main` module and starts uvicorn programmatically.
This helps avoid import path issues when launching from the workspace root.
"""
import importlib
import uvicorn

main = importlib.import_module("main")
app = getattr(main, "app")

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
