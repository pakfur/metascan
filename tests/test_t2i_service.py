"""T2iService: async wrappers over the t2i_images DB methods and file trashing."""

from __future__ import annotations

import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from unittest import mock

from backend.services.t2i_service import T2iService
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media


class ServiceCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.db = DatabaseManager(self.root / "db")
        self.addCleanup(self.db.close)
        self.service = T2iService(self.db)

    def add(self, name: str, **facts: Any) -> int:
        """A t2i_images row whose file exists on disk and in the library."""
        path = self.root / name
        path.write_bytes(b"png")
        self.db.save_media(
            Media(
                file_path=path,
                file_size=1,
                width=8,
                height=8,
                format="png",
                created_at=datetime.now(),
                modified_at=datetime.now(),
            )
        )
        return self.db.create_t2i_image(file_path=str(path), **facts)


class TestListing(ServiceCase):
    async def test_rows_come_back_newest_first_with_a_complete_form_state(self) -> None:
        one = self.add("a.png", prompt_used="first", seed=1, model="krea2")
        two = self.add(
            "b.png",
            prompt_used="second",
            seed=2,
            form_state={"prompt": "edited", "mode": "random"},
        )
        rows = await self.service.list_images(limit=10, before_id=None)
        self.assertEqual([r["id"] for r in rows], [two, one])
        self.assertEqual(rows[0]["form_state"]["prompt"], "edited")  # stored wins
        self.assertEqual(rows[0]["form_state"]["mode"], "random")
        self.assertEqual(rows[0]["form_state"]["seed"], 2)  # gap filled from the facts
        self.assertEqual(rows[1]["form_state"]["prompt"], "first")
        self.assertEqual(rows[1]["form_state"]["mode"], "manual")
        for row in rows:
            self.assertEqual(len(row["form_state"]), 11)
            self.assertEqual(row["file_name"], row["file_path"].rsplit("/", 1)[-1])

    async def test_paging(self) -> None:
        ids = [self.add(f"{n}.png") for n in range(4)]
        page = await self.service.list_images(limit=2, before_id=None)
        older = await self.service.list_images(limit=2, before_id=page[-1]["id"])
        self.assertEqual([r["id"] for r in page + older], ids[::-1])

    async def test_paths_are_the_library_files_in_creation_order(self) -> None:
        self.add("a.png")
        self.add("b.png")
        self.db.create_t2i_image(file_path=str(self.root / "ghost.png"))  # no media row
        self.assertEqual(
            await self.service.list_paths(),
            [str(self.root / "a.png"), str(self.root / "b.png")],
        )


class TestUpdate(ServiceCase):
    async def test_the_patch_merges_over_the_complete_state(self) -> None:
        image_id = self.add("a.png", prompt_used="the prompt", seed=9, caption="c")
        row = await self.service.update_form_state(
            image_id, {"seed": 77, "negative": "x"}
        )
        assert row is not None
        self.assertEqual(row["id"], image_id)
        self.assertEqual(row["form_state"]["seed"], 77)
        self.assertEqual(row["form_state"]["negative"], "x")
        self.assertEqual(row["form_state"]["prompt"], "the prompt")  # from the facts
        self.assertEqual(row["form_state"]["caption"], "c")
        self.assertEqual(len(row["form_state"]), 11)
        stored = self.db.get_t2i_image(image_id)
        assert stored is not None
        self.assertEqual(stored["form_state"], row["form_state"])

    async def test_the_facts_are_never_written(self) -> None:
        image_id = self.add("a.png", prompt_used="the prompt", seed=9)
        await self.service.update_form_state(image_id, {"prompt": "new", "seed": 1})
        stored = self.db.get_t2i_image(image_id)
        assert stored is not None
        self.assertEqual(stored["prompt_used"], "the prompt")
        self.assertEqual(stored["seed"], 9)

    async def test_an_unknown_image_is_none(self) -> None:
        self.assertIsNone(await self.service.update_form_state(404, {"prompt": "x"}))


class TestDelete(ServiceCase):
    async def test_the_row_the_media_and_the_file_go(self) -> None:
        image_id = self.add("a.png")
        path = str(self.root / "a.png")
        with mock.patch("metascan.utils.trash.send2trash") as trash:
            self.assertTrue(await self.service.delete_image(image_id))
        trash.assert_called_once_with(path)
        self.assertIsNone(self.db.get_t2i_image(image_id))
        self.assertEqual(await self.service.list_paths(), [])

    async def test_an_unknown_image_is_false_and_trashes_nothing(self) -> None:
        with mock.patch("metascan.utils.trash.send2trash") as trash:
            self.assertFalse(await self.service.delete_image(404))
        trash.assert_not_called()

    async def test_a_file_that_is_already_gone_is_not_an_error(self) -> None:
        image_id = self.add("a.png")
        (self.root / "a.png").unlink()
        with mock.patch("metascan.utils.trash.send2trash") as trash:
            self.assertTrue(await self.service.delete_image(image_id))
        trash.assert_not_called()  # remove_files_to_trash skips a missing file


class TestOffTheEventLoop(ServiceCase):
    async def test_every_database_call_runs_in_a_worker_thread(self) -> None:
        image_id = self.add("a.png", prompt_used="p")
        main = threading.current_thread()
        seen: Dict[str, threading.Thread] = {}

        def spy(name: str) -> Any:
            original = getattr(self.db, name)

            def wrapper(*args: Any, **kwargs: Any) -> Any:
                seen[name] = threading.current_thread()
                return original(*args, **kwargs)

            return wrapper

        names: List[str] = [
            "list_t2i_images",
            "list_t2i_paths",
            "get_t2i_image",
            "set_t2i_image_form_state",
            "delete_t2i_image",
        ]
        for name in names:
            setattr(self.db, name, spy(name))
        await self.service.list_images(limit=5, before_id=None)
        await self.service.list_paths()
        await self.service.update_form_state(image_id, {"seed": 1})

        def trash_spy(path: str) -> None:
            seen["send2trash"] = threading.current_thread()

        with mock.patch("metascan.utils.trash.send2trash", side_effect=trash_spy):
            await self.service.delete_image(image_id)
        self.assertEqual(sorted(seen), sorted(names + ["send2trash"]))
        for name, thread in seen.items():
            self.assertIsNot(thread, main, name)


if __name__ == "__main__":
    unittest.main()
