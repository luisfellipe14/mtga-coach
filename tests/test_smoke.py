from pathlib import Path
import subprocess
import sys
import unittest


class LaunchTests(unittest.TestCase):
    def test_loopback_launch_serves_summary(self):
        run = Path(__file__).resolve().parents[1] / 'run.py'
        result = subprocess.run([sys.executable, str(run), '--smoke'],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('local server and API verified', result.stdout)
