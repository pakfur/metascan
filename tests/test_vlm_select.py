"""Tests for metascan.core.vlm_select.vlm_model_installed.

"Installed" means what ``VlmClient`` needs to spawn the model: the GGUF, the
multimodal projector and the llama-server binary, all on disk. The tests point
the data directory and the binary at a temp directory and use a real registry
id, so nothing here depends on this machine's models.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from metascan.core.vlm_models import REGISTRY
from metascan.core.vlm_select import vlm_model_installed

MODEL_ID = "qwen3vl-8b"


class VlmModelInstalledTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data = Path(self._tmp.name)
        self.binary = self.data / "bin" / "llama-server"
        for target, value in (
            ("metascan.utils.app_paths.get_data_dir", self.data),
            ("metascan.utils.llama_server.binary_path", self.binary),
        ):
            patcher = mock.patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.spec = REGISTRY[MODEL_ID]

    def _put(self, *parts: str) -> None:
        path = self.data.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")

    def _install(
        self, weights: bool = True, projector: bool = True, binary: bool = True
    ) -> None:
        if weights:
            self._put("models", "vlm", self.spec.gguf_filename)
        if projector:
            self._put("models", "vlm", self.spec.mmproj_filename)
        if binary:
            self._put("bin", "llama-server")

    def test_the_weights_the_projector_and_the_binary_make_it_installed(self) -> None:
        self._install()
        self.assertTrue(vlm_model_installed(MODEL_ID))

    def test_nothing_on_disk_is_not_installed(self) -> None:
        self.assertFalse(vlm_model_installed(MODEL_ID))

    def test_each_missing_piece_means_not_installed(self) -> None:
        for missing in ("weights", "projector", "binary"):
            with self.subTest(missing=missing):
                for child in sorted(self.data.rglob("*"), reverse=True):
                    child.unlink() if child.is_file() else child.rmdir()
                self._install(**{missing: False})
                self.assertFalse(vlm_model_installed(MODEL_ID))

    def test_an_id_the_registry_does_not_know_is_not_blocked(self) -> None:
        # Nothing to check it against: leave the verdict to the client.
        self.assertTrue(vlm_model_installed("some-other-model"))


if __name__ == "__main__":
    unittest.main()
