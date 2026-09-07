from pathlib import Path
import subprocess
import tempfile
import sys
import unittest


class LaunchTests(unittest.TestCase):
    def test_loopback_launch_serves_summary(self):
        run = Path(__file__).resolve().parents[1] / 'run.py'
        result = subprocess.run([sys.executable, str(run), '--smoke'],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('local server and API verified', result.stdout)


class SecondInstanceTests(unittest.TestCase):
    def test_a_second_copy_refuses_the_port_instead_of_running_as_a_ghost(self):
        """Windows lets both bind; the newer one would silently serve nothing."""
        import threading
        from mtga_coach.__main__ import already_running
        from mtga_coach.http_api import create_server
        from mtga_coach.service import CoachService

        with tempfile.TemporaryDirectory() as directory:
            service = CoachService(data_dir=Path(directory))
            server = create_server(service, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                self.assertTrue(already_running("127.0.0.1", server.server_port))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
                service.store.close()
            self.assertFalse(already_running("127.0.0.1", server.server_port))
