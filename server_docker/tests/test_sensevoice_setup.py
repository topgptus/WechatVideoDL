"""Security and UX contracts for optional SenseVoice Docker installation."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch

from server_docker.sensevoice_status import sensevoice_status
from server_docker.scripts import install_sensevoice_model as installer
from server_docker import app as appmod
from server_docker.scripts.host_transcription_worker import ensure_private_queue

ROOT = Path(__file__).resolve().parents[1]


class SenseVoiceSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.root.joinpath("transcription_queue").mkdir()
        self.model_limits = {
            "model.int8.onnx": (1, 128), "tokens.txt": (1, 128),
            "LICENSE": (0, 128), "README.md": (0, 128),
        }

    def make_archive(self, name: str | None = None, *, duplicate=False, symlink=False):
        archive = self.root / "demo.tar.bz2"
        names = {
            "model.int8.onnx": b"model", "tokens.txt": b"tokens",
            "LICENSE": b"license",
        }
        with tarfile.open(archive, "w:bz2") as out:
            for filename, content in names.items():
                member = tarfile.TarInfo(
                    f"{installer.MODEL_RELEASE}/{filename}" if name is None else name
                )
                member.size = len(content)
                out.addfile(member, io.BytesIO(content))
            if duplicate:
                duplicate_member = tarfile.TarInfo(f"{installer.MODEL_RELEASE}/tokens.txt")
                duplicate_member.size = 4
                out.addfile(duplicate_member, io.BytesIO(b"fake"))
            if symlink:
                member = tarfile.TarInfo(f"{installer.MODEL_RELEASE}/README.md")
                member.type = tarfile.SYMTYPE
                member.linkname = "../../etc/passwd"
                out.addfile(member)
        return archive

    def test_secure_offline_installer_and_idempotent_existing_model(self):
        archive = self.make_archive()
        target = self.root / "models" / "sensevoice"
        with patch.object(installer, "ALLOWED", self.model_limits):
            self.assertTrue(installer.install_model(target, archive))
            self.assertFalse(installer.install_model(target, archive))
        self.assertEqual((target / "tokens.txt").read_bytes(), b"tokens")
        self.assertEqual((target / "LICENSE").read_bytes(), b"license")
        self.assertFalse((target / "source.json").exists())

    def test_model_install_rejects_duplicate_and_traversal_without_writing(self):
        target = self.root / "models" / "sensevoice"
        with patch.object(installer, "ALLOWED", self.model_limits):
            archive = self.make_archive(duplicate=True)
            with self.assertRaisesRegex(ValueError, "INVALID_MODEL_MEMBER"):
                installer.install_model(target, archive)
            self.assertFalse(target.exists())
            archive = self.make_archive(name="../outside")
            with self.assertRaisesRegex(ValueError, "UNSAFE_MODEL_ARCHIVE"):
                installer.install_model(target, archive)
            self.assertFalse(target.exists())

    def test_model_install_rejects_sha_mismatch_and_symlink_destination(self):
        archive = self.make_archive()
        target = self.root / "models" / "sensevoice"
        with patch.object(installer, "ALLOWED", self.model_limits):
            with self.assertRaisesRegex(ValueError, "SHA256"):
                installer.install_model(target, archive, expected_sha256="0"*64)
            self.assertFalse(target.exists())
            target.parent.mkdir(exist_ok=True)
            target.symlink_to(self.root, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "SYMLINK"):
                installer.install_model(target, archive)

    def test_model_download_rejects_untrusted_sources(self):
        self.assertTrue(installer._valid_redirect(installer.OFFICIAL_ARCHIVE))
        self.assertTrue(installer._valid_redirect("https://release-assets.githubusercontent.com/test"))
        for url in ("http://github.com/test", "https://evil.github.com/test",
                    "https://example.net/bad", "file:///etc/passwd"):
            self.assertFalse(installer._valid_redirect(url))

    def test_status_is_read_only_and_does_not_expose_private_fields(self):
        self.assertEqual(sensevoice_status(self.root, False)["state"], "disabled")
        self.assertEqual(sensevoice_status(self.root, True)["state"], "offline")
        marker = self.root / "transcription_queue" / "worker.status.json"
        marker.write_text(json.dumps({"ready":True,"model":"sensevoice",
                                      "at":1000,"pid":1234, "secret":"DO_NOT_LEAK"}))
        self.assertTrue(sensevoice_status(self.root,True,now=1005)["ready"])
        self.assertNotIn("secret", str(sensevoice_status(self.root,True,now=1005)))
        self.assertNotIn("pid", str(sensevoice_status(self.root,True,now=1005)))
        self.assertFalse(sensevoice_status(self.root,True,now=1200)["ready"])
        marker.unlink()
        marker.symlink_to(self.root/"missing-status")
        self.assertEqual(sensevoice_status(self.root,True)["state"], "offline")

    def test_admin_only_status_endpoint_and_setup_ui(self):
        markup = (ROOT / "templates/settings.html").read_text("utf-8")
        self.assertIn('data-settings-panel="sensevoice"', markup)
        self.assertIn("setup_sensevoice_docker.sh", markup)
        self.assertIn("sensevoice-check-button", markup)
        client = appmod.app.test_client()
        member = {"id":"m", "role":"member","username":"member","is_superadmin":False}
        admin = {"id":"a","role":"admin","username":"admin","is_superadmin":True}
        with patch.object(appmod, "current_user",return_value=member):
            self.assertEqual(client.get("/api/admin/sensevoice-status").status_code,403)
            self.assertNotIn('data-settings-panel="sensevoice"',
                             client.get("/settings").get_data(as_text=True))
        with patch.object(appmod,"current_user",return_value=admin), \
             patch.object(appmod, "get_user_store") as store, \
             patch.object(appmod,"DATA_DIR",self.root):
            store.return_value.list_users.return_value=[]
            response=client.get("/api/admin/sensevoice-status")
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.get_json()["sensevoice"]["state"], "disabled")
            self.assertEqual(response.headers["Cache-Control"],"no-store")

    def test_host_owned_private_queue_requires_no_fowner_capability(self):
        directory = self.root / "private-queue"
        directory.mkdir(mode=0o700)
        with patch("server_docker.scripts.host_transcription_worker.os.geteuid", return_value=-1), \
             patch("server_docker.scripts.host_transcription_worker.os.chmod") as chmod:
            ensure_private_queue(directory)
            chmod.assert_not_called()
        directory.chmod(0o755)
        with patch("server_docker.scripts.host_transcription_worker.os.geteuid", return_value=-1):
            with self.assertRaisesRegex(ValueError, "UNSAFE_QUEUE_PERMISSIONS"):
                ensure_private_queue(directory)

    def test_optional_docker_worker_has_strict_volume_boundary(self):
        compose = (ROOT / "docker-compose.sensevoice.yml").read_text("utf-8")
        for expected in ('profiles: ["sensevoice"]','network_mode: "none"',
                         "read_only: true",'cap_drop: ["ALL"]',
                         "no-new-privileges:true",'cap_add: ["DAC_OVERRIDE"]',"./data/downloads:/data/downloads",
                         "./data/transcription_queue:/data/transcription_queue",
                         "./models/sensevoice:/models/sensevoice:ro"):
            self.assertIn(expected,compose)
        self.assertNotIn("./data:/data",compose)
        self.assertNotIn("docker.sock",compose)
        self.assertNotIn("yuanbao",compose.lower())
        script = (ROOT / "scripts/setup_sensevoice_docker.sh").read_text("utf-8")
        self.assertIn("--enable-app",script)
        self.assertIn("install_sensevoice_model.py",script)
        self.assertNotIn("down -v",script)


if __name__ == "__main__":
    unittest.main()
