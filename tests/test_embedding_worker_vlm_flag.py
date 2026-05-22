"""Verify embedding_queue honors tag_with_vlm flag in task config.

Smoke test only — actual worker behavior is exercised by the existing
test_embedding_pipeline.py. We just check that the task-config hook is
plumbed through and that the start_indexing signature accepts the flag.
"""

import tempfile
from pathlib import Path

from metascan.core.embedding_queue import EmbeddingQueue


def test_start_indexing_accepts_tag_with_vlm_kwarg():
    with tempfile.TemporaryDirectory() as tmp:
        eq = EmbeddingQueue()
        eq._queue_dir = Path(tmp)
        # empty file_paths short-circuits to on_complete; we just want to
        # confirm the kwarg parses without TypeError.
        result = eq.start_indexing(
            file_paths=[],
            clip_model_key="small",
            db_path=tmp,
            tag_with_vlm=True,
        )
        assert result is True or result is None  # implementation returns True


def test_task_file_records_tag_with_vlm_flag():
    """When start_indexing actually spawns, the task JSON must carry the flag.

    We block the worker from running by patching subprocess.Popen to a no-op
    so we can read the task file before it's consumed.
    """
    import json
    from unittest.mock import patch, MagicMock

    with tempfile.TemporaryDirectory() as tmp:
        eq = EmbeddingQueue()
        eq._queue_dir = Path(tmp)
        # Need to give it at least one file path so it doesn't short-circuit.
        fake_image = Path(tmp) / "fake.jpg"
        fake_image.write_bytes(b"\xff\xd8\xff\xd9")

        fake_proc = MagicMock()
        fake_proc.poll.return_value = None
        with patch("subprocess.Popen", return_value=fake_proc):
            eq.start_indexing(
                file_paths=[str(fake_image)],
                clip_model_key="small",
                db_path=tmp,
                tag_with_vlm=True,
            )
        task_file = Path(tmp) / "embedding_task.json"
        assert task_file.exists()
        with open(task_file) as f:
            task = json.load(f)
        assert task.get("tag_with_vlm") is True
