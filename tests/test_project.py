"""Offline configuration, matching and HTTP contract regressions; no face data required."""

import ast
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
from threading import Thread
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

from project_config import ROOT, load_settings, validate_identity, dataset_identity, best_distance_match, notify_controller


def isolated_function(filename, name, namespace):
    tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), filename, "exec"), namespace)
    return namespace[name]


class ConfigurationTests(unittest.TestCase):
    def test_config_and_environment_overrides(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.ini"
            path.write_text("[devices]\ncontroller_url=http://192.0.2.1/\n[paths]\ndataset_dir=dataset\n")
            settings = load_settings(path, {"ESP32_CAM_URL": "http://192.0.2.2:81/stream"})
            self.assertEqual(settings.controller_url, "http://192.0.2.1")
            self.assertEqual(settings.camera_stream_url, "http://192.0.2.2:81/stream")
            self.assertEqual(settings.dataset_dir, ROOT / "dataset")

    def test_invalid_config_is_rejected(self):
        with self.assertRaises(ValueError):
            load_settings(environ={"ESP32_CAM_URL": "not-a-url"})
        with self.assertRaises(ValueError):
            load_settings(environ={"FACE_CAPTURE_TIMEOUT": "nan"})

    def test_identity_prevents_path_escape(self):
        for value in ("../outside", "A/B", "A\\B", "", ".", "!FAIL"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_identity(value)
        self.assertEqual(validate_identity("Jean_Luc-2"), "Jean_Luc-2")

    def test_multiword_identity_is_preserved(self):
        self.assertEqual(dataset_identity("Jean_Luc_front.jpg"), "Jean_Luc")
        self.assertEqual(dataset_identity("Jean_Luc.jpg"), "Jean_Luc")

    def test_empty_and_invalid_distances_do_not_match(self):
        self.assertIsNone(best_distance_match([], [], 0.5))
        self.assertIsNone(best_distance_match([float("nan")], ["User01"], 0.5))
        self.assertIsNone(best_distance_match([0.6], ["User01"], 0.5))
        self.assertEqual(best_distance_match([0.7, 0.5], ["A", "B"], 0.5), "B")


class RecognitionRegressionTests(unittest.TestCase):
    def test_both_backends_skip_camera_when_database_is_empty(self):
        for filename in ("recognitionFace.py", "InsightFace.py"):
            camera = Mock(side_effect=AssertionError("Camera must not be opened"))
            fn = isolated_function(filename, "scan_face_slowly", {"cv2": SimpleNamespace(VideoCapture=camera)})
            args = (None, None, [], []) + ((None,) if filename == "InsightFace.py" else ())
            self.assertIsNone(fn(*args))
            camera.assert_not_called()

    def test_camera_is_released_when_recognition_raises(self):
        for filename in ("recognitionFace.py", "InsightFace.py"):
            with self.subTest(filename=filename):
                capture = Mock()
                capture.isOpened.return_value = True
                capture.read.return_value = (True, object())
                cv = SimpleNamespace(VideoCapture=lambda _: capture, resize=Mock(side_effect=RuntimeError("decode error")))
                clock = SimpleNamespace(time=Mock(side_effect=[0, 10]))
                namespace = {"cv2": cv, "URL_STREAM": "http://example.invalid/stream", "time": clock}
                fn = isolated_function(filename, "scan_face_slowly", namespace)
                args = (None, None, [object()], ["User01"])
                if filename == "InsightFace.py":
                    model = Mock()
                    model.get.side_effect = RuntimeError("decode error")
                    args += (model,)
                with self.assertRaises(RuntimeError):
                    fn(*args)
                capture.release.assert_called_once()


class ControllerContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.received = None
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                cls.received = urlsplit(self.path)
                self.send_response(503 if cls.received.path == "/full" else 200)
                self.end_headers()
                self.wfile.write(b"QUEUE_FULL" if cls.received.path == "/full" else b"CMD_SENT")
            def log_message(self, *args):
                pass
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_names_are_url_encoded(self):
        self.assertTrue(notify_controller(self.url + "/open", {"name": "User01  12:30"}))
        self.assertEqual(parse_qs(type(self).received.query)["name"], ["User01  12:30"])

    def test_full_queue_is_not_reported_as_accepted(self):
        self.assertFalse(notify_controller(self.url + "/full"))


if __name__ == "__main__":
    unittest.main()
