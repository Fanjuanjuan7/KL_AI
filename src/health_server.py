import http.server
import socketserver
import threading
import json

class HealthHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            response = {"status": "ok", "app": "KL_AI"}
            self.wfile.write(json.dumps(response).encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress logging

def start_health_server(port=9999):
    def run():
        try:
            # allow_reuse_address allows restarting the server immediately
            socketserver.TCPServer.allow_reuse_address = True
            with socketserver.TCPServer(("", port), HealthHandler) as httpd:
                # print(f"Health check server running at http://localhost:{port}/health") # Silenced per user request
                httpd.serve_forever()
        except OSError as e:
            # print(f"Health server failed to start on port {port}: {e}") # Silenced per user request
            pass

    t = threading.Thread(target=run, daemon=True)
    t.start()
