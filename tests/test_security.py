import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from mtga_coach.http_api import create_server
from mtga_coach.service import CoachService


class LocalBoundaryTests(unittest.TestCase):
    def test_rejects_network_bind_and_untrusted_http_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            service = CoachService(data_dir=Path(directory))
            with self.assertRaises(ValueError):
                create_server(service, host='0.0.0.0', port=0)
            server = create_server(service, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.server_port

            def send(method, path, payload=None, extra=None):
                headers = {'Host': f'127.0.0.1:{port}', 'Content-Type': 'application/json',
                           'X-MTGA-Coach': '1', **(extra or {})}
                body = json.dumps(payload).encode() if payload is not None else None
                client = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
                try:
                    client.request(method, path, body=body, headers=headers)
                    response = client.getresponse()
                    data = response.read()
                    return response.status, data
                finally:
                    client.close()

            try:
                self.assertEqual(send('GET', '/api/health', extra={'Host': f'evil.example:{port}'})[0], 400)
                self.assertEqual(send('POST', '/api/import', {'source': 'configured'},
                                      {'Origin': 'https://evil.example'})[0], 400)
                self.assertEqual(send('POST', '/api/import', {'source': 'configured'},
                                      {'X-MTGA-Coach': ''})[0], 400)
                self.assertEqual(send('POST', '/api/import', {'path': 'C:/private.log'})[0], 400)
                self.assertEqual(send('GET', '/../run.py')[0], 404)
                self.assertEqual(send('GET', '/static/')[0], 404)
                status, page = send('GET', '/')
                self.assertEqual(status, 200)
                self.assertIn(b'src="/app.js"', page)
                self.assertNotIn(b'<script src="http', page)
                self.assertEqual(service.store.import_count(), 0)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
                service.store.close()
