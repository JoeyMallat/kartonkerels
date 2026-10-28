"""Kartonkerels application entry point. Run with `python app.py`."""
import os
from backend.core import app
from backend.routes import catalog, inventory, pricing, accounts, operations

for module in (catalog, inventory, pricing, accounts, operations):
    app.register_blueprint(module.bp)


@app.get("/health")
def health():
    return {"ok": True}


if __name__ == "__main__":
    from waitress import serve

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "5000"))
    print(f"Kartonkerels running on http://{host}:{port}")
    serve(app, host=host, port=port, threads=int(os.getenv("THREADS", "8")))
