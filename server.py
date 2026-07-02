"""Mini server HTTP di health-check, utile per hosting che richiedono una
porta aperta (Render, Railway, Replit, uptime monitor...). Si avvia solo se
la variabile d'ambiente PORT e' impostata."""

import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("health")


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"OK - Aster DEX trading bot attivo"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silenzia il log per ogni richiesta
        pass


def start_health_server(port: int):
    server = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info("Health-check server in ascolto sulla porta %s", port)
    return server


if __name__ == "__main__":
    import os
    logging.basicConfig(level=logging.INFO)
    start_health_server(int(os.getenv("PORT", "8080")))
    threading.Event().wait()
