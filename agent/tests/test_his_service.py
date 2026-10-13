import unittest
from unittest.mock import Mock, patch

from medrax.his_service import DEFAULT_HIS_URL, managed_his_service


class HisServiceTest(unittest.TestCase):
    @patch("medrax.his_service.subprocess.Popen")
    @patch("medrax.his_service.probe_his")
    def test_existing_service_is_reused_not_stopped(self, probe, popen):
        with managed_his_service(DEFAULT_HIS_URL):
            pass
        popen.assert_not_called()

    @patch("medrax.his_service.subprocess.Popen")
    @patch("medrax.his_service.probe_his", side_effect=ConnectionError("unavailable"))
    def test_unavailable_explicit_endpoint_does_not_fall_back(self, probe, popen):
        with self.assertRaises(RuntimeError):
            with managed_his_service("http://127.0.0.1:8080"):
                pass
        popen.assert_not_called()

    @patch("medrax.his_service.subprocess.Popen")
    @patch("medrax.his_service.probe_his", side_effect=[ConnectionError("unavailable"), None])
    def test_mock_started_here_is_stopped_on_exit(self, probe, popen):
        process = Mock()
        process.poll.return_value = None
        popen.return_value = process
        with managed_his_service(DEFAULT_HIS_URL):
            popen.assert_called_once()
        process.terminate.assert_called_once()
        process.wait.assert_called_once()
