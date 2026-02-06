"""
Health check server module for KL_AI application.

Provides a lightweight HTTP server for health check endpoints,
useful for monitoring application status in containerized environments.

Example:
    >>> from health_server import start_health_server
    >>> start_health_server(port=9999, verbose=True)
"""

import http.server
import socketserver
import threading
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class HealthHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP request handler for health check endpoints."""

    def do_GET(self) -> None:
        """Handle GET requests to /health endpoint."""
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            response = {"status": "ok", "app": "KL_AI"}
            self.wfile.write(json.dumps(response).encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        """Suppress default HTTP logging."""
        pass


def start_health_server(port: int = 9999, verbose: bool = False) -> Optional[threading.Thread]:
    """
    Start the health check server in a background thread.

    Args:
        port: Port number for the health server (default: 9999)
        verbose: Whether to log startup messages (default: False)

    Returns:
        The thread running the server, or None if failed to start

    Raises:
        No exceptions raised; failures are handled gracefully
    """
    def run() -> None:
        attempt = 0
        max_attempts = 3
        current_port = port

        while attempt < max_attempts:
            try:
                socketserver.TCPServer.allow_reuse_address = True
                with socketserver.TCPServer(("", current_port), HealthHandler) as httpd:
                    if verbose:
                        logger.info(f"Health check server running at http://localhost:{current_port}/health")
                    httpd.serve_forever()
                    return
            except OSError as e:
                attempt += 1
                if e.errno == 48:  # Address already in use
                    if verbose:
                        logger.warning(f"Port {current_port} in use, trying port {current_port + 1}")
                    current_port += 1
                elif attempt < max_attempts:
                    if verbose:
                        logger.warning(f"Health server failed to start on port {current_port}: {e}, retrying...")
                else:
                    if verbose:
                        logger.error(f"Health server failed to start after {max_attempts} attempts: {e}")
                    break

    t = threading.Thread(target=run, daemon=True, name="HealthServer")
    t.start()
    return t
