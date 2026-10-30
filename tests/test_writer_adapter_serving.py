"""Prompt-writer adapters served by the writer llama-server.

The writer's llama-server spawns with ``--lora <file>`` for every servable
adapter plus ``--lora-init-without-apply``; an adapter request carries
``"lora": [{"id": <position>, "scale": 1.0}]`` and a base request carries no
``lora`` field at all. A registry change restarts the writer only when it is
idle. Outputs written with adapter N record ``writer_id = adapter:N``.

The llama-server here is ``tests/_fake_llama_server.py`` (it records its argv
and every request body); all prompts are synthetic.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional
from unittest import mock

import pytest

from backend.services.vlm_roles import AdapterSlot, VlmRoles
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.vlm_client import (
    AdapterLoadingError,
    AdapterNotLoadedError,
    VlmClient,
)
from metascan.core.vlm_models import get_spec
from metascan.core.writer_adapters import (
    ADAPTER_GGUF,
    AdapterFile,
    WriterAdapterUnavailable,
    adapter_dir,
    adapter_writer_id,
    servable_adapter_files,
)
from tests._fake_llama_server import fake_llama_command

BASE = "qwen3vl-8b"


# ---- helpers ---------------------------------------------------------------


def _gguf(tmp: Path, adapter_id: int) -> Path:
    path = adapter_dir(tmp, adapter_id) / ADAPTER_GGUF
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"GGUF")
    return path


class Recorder:
    """Reads what the fake llama-servers recorded: one ``argv`` line per
    spawn, one ``body`` line per chat request."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _entries(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]

    def spawns(self) -> List[List[str]]:
        return [e["argv"] for e in self._entries() if "argv" in e]

    def bodies(self) -> List[Dict[str, Any]]:
        return [e["body"] for e in self._entries() if "body" in e]


def _loras_of(argv: List[str]) -> List[str]:
    return [argv[i + 1] for i, a in enumerate(argv) if a == "--lora"]


def _factory(record: Path, reply_delay_ms: int = 0):
    """Real VlmClients whose llama-server is the fake, spawned with the
    real command's adapter flags appended (so the fake records them)."""

    def make() -> VlmClient:
        c = VlmClient()
        real = c._build_command

        def build(spec: Any, port: int) -> List[str]:
            cmd = real(spec, port)
            start = next(
                (i for i, a in enumerate(cmd) if a.startswith("--lora")), len(cmd)
            )
            return fake_llama_command(
                port,
                canned_response="SYNTH prompt",
                reply_delay_ms=reply_delay_ms,
                record=record,
                extra_args=cmd[start:],
            )

        c._build_command = build  # type: ignore[method-assign]
        return c

    return make


class Source:
    """A mutable adapter registry for VlmRoles."""

    def __init__(self, files: List[AdapterFile]) -> None:
        self.files = list(files)

    def __call__(self) -> List[AdapterFile]:
        return list(self.files)


def _roles(record: Path, source: Source, reply_delay_ms: int = 0) -> VlmRoles:
    return VlmRoles(
        writer=BASE,
        general=BASE,
        client_factory=_factory(record, reply_delay_ms),
        installed=lambda _mid: True,
        swap_grace_s=0.0,
        adapter_source=source,
    )


async def _until(predicate: Any, what: str, timeout: float = 10.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.01)


async def _ask(client: VlmClient, **kw: Any) -> str:
    return await client.generate_text(
        system_prompt="SYNTH system", user_prompt="SYNTH user", **kw
    )


@pytest.fixture
async def setup(tmp_path: Path):
    a, b = _gguf(tmp_path, 1), _gguf(tmp_path, 2)
    source = Source([AdapterFile(1, "t2i", BASE, a), AdapterFile(2, "t2i", BASE, b)])
    record = tmp_path / "record.jsonl"
    made: List[VlmRoles] = []

    def build(reply_delay_ms: int = 0) -> VlmRoles:
        roles = _roles(record, source, reply_delay_ms)
        made.append(roles)
        return roles

    yield build, source, Recorder(record), a, b
    for roles in made:
        await roles.shutdown_all()


# ---- spawn command ---------------------------------------------------------


def test_spawn_command_lists_every_adapter_then_init_without_apply(
    tmp_path: Path,
) -> None:
    a, b = tmp_path / "a.gguf", tmp_path / "b.gguf"
    spec = get_spec(BASE)
    cmd = VlmClient(lora_paths=[a, b])._build_command(spec, 1234)
    assert cmd[-5:] == [
        "--lora",
        str(a),
        "--lora",
        str(b),
        "--lora-init-without-apply",
    ]
    base = VlmClient()._build_command(spec, 1234)
    assert not any(arg.startswith("--lora") for arg in base)
    assert cmd[: len(base)] == base


def test_servable_adapter_files_reads_the_registry(tmp_path: Path) -> None:
    db = DatabaseManager(tmp_path / "db")
    try:
        ids = {
            status: db.create_writer_adapter(
                name=f"n-{status}",
                scope="all",
                kind="t2i",
                base_model=BASE,
                status=status,
            )
            for status in ("ready", "untested", "training", "failed", "paused")
        }
        no_file = db.create_writer_adapter(
            name="no-file", scope="all", kind="t2i", base_model=BASE, status="ready"
        )
        gone = db.create_writer_adapter(
            name="gone", scope="all", kind="i2v", base_model=BASE, status="ready"
        )
        for aid in list(ids.values()) + [gone]:
            _gguf(tmp_path, aid)
        db.soft_delete_writer_adapter(gone)
        files = servable_adapter_files(db, tmp_path)
        assert [f.adapter_id for f in files] == [ids["ready"], ids["untested"]]
        assert no_file not in [f.adapter_id for f in files]
        assert files[0].path == adapter_dir(tmp_path, ids["ready"]) / ADAPTER_GGUF
    finally:
        db.close()


# ---- requests ---------------------------------------------------------------


async def test_base_request_has_no_lora_key_and_adapter_request_its_position(
    setup: Any,
) -> None:
    build, _source, rec, a, b = setup
    roles = build()
    await roles.refresh_adapters()
    assert roles.writer_adapters() == [AdapterSlot(1, 0, a), AdapterSlot(2, 1, b)]
    assert roles.adapter_path(2) == b and roles.adapter_path(9) is None
    client = roles.client("writer")
    await client.ensure_started(BASE)

    await _ask(client)
    await _ask(client, lora_path=b)
    await _ask(client, lora_path=a)
    await _ask(client, lora_id=1)

    spawns = rec.spawns()
    assert len(spawns) == 1
    assert _loras_of(spawns[0]) == [str(a), str(b)]
    assert "--lora-init-without-apply" in spawns[0]
    base, second, first, by_id = rec.bodies()
    # The base request is the pre-adapter shape, key for key.
    assert set(base) == {"messages", "max_tokens", "temperature"}
    assert second["lora"] == [{"id": 1, "scale": 1.0}]
    assert first["lora"] == [{"id": 0, "scale": 1.0}]
    assert by_id["lora"] == [{"id": 1, "scale": 1.0}]
    assert {k: v for k, v in second.items() if k != "lora"} == base


async def test_a_general_only_client_loads_no_adapters(tmp_path: Path) -> None:
    a = _gguf(tmp_path, 1)
    roles = VlmRoles(
        writer=BASE,
        general="qwen3vl-4b",
        client_factory=VlmClient,
        installed=lambda _mid: True,
        adapter_source=Source([AdapterFile(1, "t2i", BASE, a)]),
    )
    await roles.refresh_adapters()
    assert roles.client("writer").lora_paths == (a,)
    assert roles.client("general").lora_paths == ()
    # An adapter built on another model is never loaded by this writer.
    other = VlmRoles(
        writer="qwen3vl-4b",
        general="qwen3vl-4b",
        client_factory=VlmClient,
        installed=lambda _mid: True,
        adapter_source=Source([AdapterFile(1, "t2i", BASE, a)]),
    )
    await other.refresh_adapters()
    assert other.client("writer").lora_paths == ()
    assert other.adapter_path(1) is None


# ---- restarts -----------------------------------------------------------------


async def test_an_idle_writer_restarts_at_once_without_a_deleted_adapter(
    setup: Any,
) -> None:
    build, _source, rec, a, b = setup
    roles = build()
    await roles.refresh_adapters()
    client = roles.client("writer")
    await client.ensure_started(BASE)
    assert await roles.withdraw_adapter(1) is True
    assert not roles.restart_pending
    assert client.state == "stopped" and client.swapped_out
    # The next request brings it back with the new list.
    await _ask(client, lora_path=b)
    assert [_loras_of(s) for s in rec.spawns()] == [[str(a), str(b)], [str(b)]]
    assert rec.bodies()[-1]["lora"] == [{"id": 0, "scale": 1.0}]
    with pytest.raises(AdapterNotLoadedError):
        await _ask(client, lora_path=a)


async def test_a_refused_withdraw_keeps_the_adapter_usable(
    setup: Any,
) -> None:
    # The DELETE answers 409 adapter_files_busy when the writer still maps
    # the GGUF: the adapter was NOT deleted, so it must stay servable (no
    # withdrawn id left behind, no restart without it).
    build, _source, rec, a, b = setup
    roles = build(reply_delay_ms=600)
    await roles.refresh_adapters()
    client = roles.client("writer")
    await client.ensure_started(BASE)
    task = asyncio.create_task(_ask(client, lora_path=a))
    await _until(lambda: client.in_flight == 1, "the request to start")

    assert await roles.withdraw_adapter(1) is False  # still mapped
    assert roles._withdrawn == set()
    assert roles.adapter_path(1) == a
    assert client.lora_paths == (a, b)
    assert await task == "SYNTH prompt"
    await _until(lambda: not roles.restart_pending, "any pending restart")
    assert client.state == "ready"
    # Still served on the same server: no respawn without it.
    assert await _ask(client, lora_path=a) == "SYNTH prompt"
    assert len(rec.spawns()) == 1


async def test_a_hold_defers_the_restart_until_it_ends(setup: Any) -> None:
    build, source, rec, a, b = setup
    source.files = source.files[:1]
    roles = build()
    await roles.refresh_adapters()
    client = roles.client("writer")
    await client.ensure_started(BASE)
    c = _gguf(rec.path.parent, 3)
    source.files.append(AdapterFile(3, "t2i", BASE, c))
    with client.hold():  # a T2I Random batch writing its prompts
        await roles.refresh_adapters()
        assert roles.restart_pending
        await asyncio.sleep(0.2)
        assert client.state == "ready"
    await _until(lambda: not roles.restart_pending, "the restart after the hold")
    assert client.state == "stopped"
    await _ask(client, lora_path=c)
    assert [_loras_of(s) for s in rec.spawns()] == [[str(a)], [str(a), str(c)]]
    assert rec.bodies()[-1]["lora"] == [{"id": 1, "scale": 1.0}]


async def test_a_new_adapter_under_another_callers_hold_is_refused_until_it_ends(
    setup: Any,
) -> None:
    # Someone else holds the writer (a T2I Random batch prompting): a request
    # for an adapter added since the spawn never restarts it under the hold.
    # It is refused as "loading" and the restart waits for the hold to end.
    build, source, rec, a, b = setup
    source.files = source.files[:1]
    roles = build()
    await roles.refresh_adapters()
    client = roles.client("writer")
    client.lora_wait_s = 0.2
    await client.ensure_started(BASE)
    source.files.append(AdapterFile(2, "t2i", BASE, b))
    held, release = asyncio.Event(), asyncio.Event()

    async def other_batch() -> None:  # its own task: its hold is not ours
        with client.hold():
            held.set()
            await release.wait()

    holder = asyncio.create_task(other_batch())
    await held.wait()
    await roles.refresh_adapters()
    assert roles.restart_pending
    with pytest.raises(AdapterLoadingError):
        await _ask(client, lora_path=b)
    assert client.state == "ready"
    assert roles.restart_pending
    release.set()
    await holder
    await _until(lambda: not roles.restart_pending, "the restart after the hold")
    assert client.state == "stopped"
    await _ask(client, lora_path=b)
    assert [_loras_of(s) for s in rec.spawns()] == [[str(a)], [str(a), str(b)]]
    assert rec.bodies()[-1]["lora"] == [{"id": 1, "scale": 1.0}]


async def test_the_callers_own_hold_does_not_block_its_adapter(setup: Any) -> None:
    # A Random batch holds the writer and asks for its own (new) adapter:
    # its hold is not someone else's work, so the writer restarts at once.
    build, source, rec, a, b = setup
    source.files = source.files[:1]
    roles = build()
    await roles.refresh_adapters()
    client = roles.client("writer")
    client.lora_wait_s = 5.0
    await client.ensure_started(BASE)
    client.lora_paths = (a, b)
    loop = asyncio.get_running_loop()
    began = loop.time()
    with client.hold():
        await _ask(client, lora_path=b)
    assert loop.time() - began < 3.0
    assert rec.bodies()[-1]["lora"] == [{"id": 1, "scale": 1.0}]


async def test_parallel_requests_for_a_new_adapter_restart_once(setup: Any) -> None:
    build, source, rec, a, b = setup
    roles = build()
    await roles.refresh_adapters()
    client = roles.client("writer")
    client.lora_wait_s = 5.0
    await client.ensure_started(BASE)
    c = _gguf(rec.path.parent, 3)
    client.lora_paths = (a, b, c)
    replies = await asyncio.gather(*(_ask(client, lora_path=c) for _ in range(3)))
    assert replies == ["SYNTH prompt"] * 3
    assert len(rec.spawns()) == 2  # one restart, not one per caller
    assert [body["lora"] for body in rec.bodies()] == [[{"id": 2, "scale": 1.0}]] * 3


async def test_a_new_adapter_asked_for_while_idle_restarts_then_serves(
    setup: Any,
) -> None:
    build, source, rec, a, b = setup
    roles = build()
    await roles.refresh_adapters()
    client = roles.client("writer")
    await client.ensure_started(BASE)
    c = _gguf(rec.path.parent, 3)
    client.lora_paths = (a, b, c)  # registry applied, restart not yet done
    await _ask(client, lora_path=c)
    assert [_loras_of(s) for s in rec.spawns()] == [
        [str(a), str(b)],
        [str(a), str(b), str(c)],
    ]
    assert rec.bodies()[-1]["lora"] == [{"id": 2, "scale": 1.0}]


async def test_an_in_flight_request_is_never_evicted_for_a_new_adapter(
    setup: Any,
) -> None:
    build, source, rec, a, b = setup
    roles = build(reply_delay_ms=800)
    await roles.refresh_adapters()
    client = roles.client("writer")
    client.lora_wait_s = 0.2
    await client.ensure_started(BASE)
    slow = asyncio.create_task(_ask(client))
    await _until(lambda: client.in_flight == 1, "the request to start")
    c = _gguf(rec.path.parent, 3)
    client.lora_paths = (a, b, c)
    with pytest.raises(AdapterLoadingError):
        await _ask(client, lora_path=c)
    assert await slow == "SYNTH prompt"
    assert len(rec.spawns()) == 1


async def test_withdrawing_an_adapter_a_loading_writer_has_is_refused(
    tmp_path: Path,
) -> None:
    a = _gguf(tmp_path, 1)
    roles = VlmRoles(
        writer=BASE,
        general=BASE,
        client_factory=VlmClient,
        installed=lambda _mid: True,
        adapter_source=Source([AdapterFile(1, "t2i", BASE, a)]),
    )
    await roles.refresh_adapters()
    client = roles.client("writer")
    # Spawned with the adapter, still loading: it maps the file.
    client._state = "loading"
    client._loaded_lora_paths = (a,)
    try:
        assert await roles.withdraw_adapter(1) is False
        # Refused: the adapter stays on the list (nothing deleted).
        assert roles._withdrawn == set() and client.lora_paths == (a,)
    finally:
        client._state = "stopped"
        await roles.shutdown_all()


async def test_withdrawn_ids_are_forgotten_once_the_registry_drops_them(
    setup: Any,
) -> None:
    build, source, rec, a, b = setup
    roles = build()
    await roles.refresh_adapters()
    await roles.withdraw_adapter(1)
    assert roles.adapter_path(1) is None
    source.files = source.files[1:]  # the delete went through
    await roles.refresh_adapters()
    assert roles._withdrawn == set()


async def test_an_explicitly_unloaded_writer_just_gets_the_new_list(
    setup: Any,
) -> None:
    build, source, rec, a, b = setup
    roles = build()
    await roles.refresh_adapters()
    client = roles.client("writer")
    await client.ensure_started(BASE)
    await client.shutdown()
    await roles.withdraw_adapter(2)
    assert not roles.restart_pending
    assert client.lora_paths == (a,) and client.state == "stopped"
    assert not client.swapped_out  # nothing brings it back on its own
    await client.ensure_started(BASE)
    assert [_loras_of(s) for s in rec.spawns()] == [[str(a), str(b)], [str(a)]]


# ---- T2I lineage --------------------------------------------------------------


import tests.test_i2v_runner as i2v_tests  # noqa: E402
import tests.test_t2i_runner as t2i_tests  # noqa: E402
import tests.test_t2i_variants as variant_tests  # noqa: E402


def _helpers(*classes: type) -> Dict[str, Any]:
    """The non-test members of ``classes`` (later ones win): their fixtures
    without re-running the test methods they define."""
    out: Dict[str, Any] = {}
    for cls in classes:
        for key, value in vars(cls).items():
            if not key.startswith("test_") and not key.startswith("__"):
                out[key] = value
    return out


_T2iBase: Any = type(
    "_T2iBase",
    (t2i_tests.RunnerCase,),
    _helpers(t2i_tests.BatchCase, t2i_tests.AccountingCase, variant_tests.VariantCase),
)
_I2vBase: Any = type(
    "_I2vBase",
    (i2v_tests.I2vRunnerBase,),
    {
        k: v
        for k, v in _helpers(i2v_tests.TestFormStateAtIngest).items()
        if k != "setUp"  # it calls super(): replayed in TestI2vAdapterLineage
    },
)


def _image_rows(db: Any) -> List[Dict[str, Any]]:
    with db.lock, db._get_connection() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT writer_id, writer_model, draft_prompt FROM t2i_images "
                "ORDER BY id"
            )
        ]


class AdapterCase(_T2iBase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.adapter = self.db.create_writer_adapter(
            name="taste-all-v1",
            scope="all",
            kind="t2i",
            base_model=BASE,
            status="untested",
        )
        self.wid = adapter_writer_id(self.adapter)
        self.gguf = self.root / "adapter.gguf"
        self.served: Dict[int, Path] = {self.adapter: self.gguf}
        self.runner.writer_adapter = self.served.get


class TestT2iAdapterLineage(AdapterCase):
    async def test_random_batch_records_the_adapter_and_sends_its_lora(self) -> None:
        assert self.vlm is not None
        self.roomy()
        await self.run_to_end(
            self.random_mode(
                batch_size=2, count_per_batch=1, variants=2, writer=self.wid
            )
        )
        await self.finish_all()
        rows = _image_rows(self.db)
        self.assertEqual(len(rows), 4)
        for row in rows:
            self.assertEqual(row["writer_id"], self.wid)
            self.assertEqual(row["writer_model"], BASE)
        self.assertEqual(len(self.vlm.calls), 4)
        for call in self.vlm.calls:
            self.assertEqual(call["lora_path"], self.gguf)

    async def test_base_writer_calls_carry_no_adapter(self) -> None:
        assert self.vlm is not None
        self.roomy()
        await self.run_to_end(self.random_mode(batch_size=1, count_per_batch=1))
        await self.runner.generate_prompt(caption="a red kite", seed=1, model="krea2")
        self.assertTrue(self.vlm.calls)
        for call in self.vlm.calls:
            self.assertNotIn("lora_path", call)
            self.assertNotIn("lora_id", call)

    async def test_manual_generate_prompt_drafts_carry_the_adapter(self) -> None:
        assert self.vlm is not None
        self.roomy()
        result = await self.runner.generate_prompt(
            caption="a red kite", seed=1, model="krea2", variants=2, writer=self.wid
        )
        self.assertEqual([d.writer_id for d in result.drafts], [self.wid] * 2)
        self.assertEqual({d.writer_model for d in result.drafts}, {BASE})
        self.assertEqual([c["lora_path"] for c in self.vlm.calls], [self.gguf] * 2)
        draft = result.drafts[0]
        await self.run_to_end(
            self.manual(
                count_per_batch=1,
                prompts=[
                    {
                        "prompt": draft.prompt + " edited",
                        "negative": None,
                        "draft": draft.prompt,
                        "writer_id": draft.writer_id,
                        "writer_model": draft.writer_model,
                        "variant_index": 0,
                    }
                ],
            )
        )
        await self.finish_all()
        rows = _image_rows(self.db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["writer_id"], self.wid)
        self.assertEqual(rows[0]["writer_model"], BASE)

    async def test_an_adapter_that_is_gone_is_unavailable(self) -> None:
        self.db.soft_delete_writer_adapter(self.adapter)
        with self.assertRaises(WriterAdapterUnavailable):
            await self.runner.generate_prompt(
                caption="a red kite", seed=1, model="krea2", writer=self.wid
            )
        with self.assertRaises(WriterAdapterUnavailable):
            await self.start(self.random_mode(writer=self.wid))

    async def test_an_adapter_the_writer_does_not_serve_is_unavailable(self) -> None:
        self.served.clear()
        with self.assertRaises(WriterAdapterUnavailable):
            await self.runner.generate_prompt(
                caption="a red kite", seed=1, model="krea2", writer=self.wid
            )
        i2v = self.db.create_writer_adapter(
            name="i2v-one", scope="all", kind="i2v", base_model=BASE, status="ready"
        )
        self.served[i2v] = self.gguf
        with self.assertRaises(WriterAdapterUnavailable):
            await self.runner.generate_prompt(
                caption="a red kite",
                seed=1,
                model="krea2",
                writer=adapter_writer_id(i2v),
            )

    async def test_an_adapter_on_another_base_model_is_unavailable(self) -> None:
        assert self.vlm is not None
        self.vlm.model_id = "qwen3vl-4b"
        with self.assertRaises(WriterAdapterUnavailable):
            await self.runner.generate_prompt(
                caption="a red kite", seed=1, model="krea2", writer=self.wid
            )
        self.assertEqual(self.vlm.calls, [])


class TestT2iAdapterServing(AdapterCase):
    def prompts_from(self, writer_id: str) -> List[Dict[str, Any]]:
        return [
            {
                "prompt": "SYNTH prompt edited",
                "negative": None,
                "draft": "SYNTH prompt",
                "writer_id": writer_id,
                "writer_model": BASE,
                "variant_index": 0,
            }
        ]

    async def test_a_draft_from_a_gone_adapter_is_adapter_unavailable(self) -> None:
        # A stale client re-rendering a deleted adapter's draft gets the
        # fallback path (409), not a 400.
        self.db.soft_delete_writer_adapter(self.adapter)
        with self.assertRaises(WriterAdapterUnavailable):
            await self.start(
                self.manual(count_per_batch=1, prompts=self.prompts_from(self.wid))
            )

    async def test_a_random_batch_prepares_its_adapter_before_holding(self) -> None:
        assert self.vlm is not None
        order: List[Any] = []

        async def prepare_lora(path: Path, wait_s: Optional[float] = None) -> None:
            order.append(("prepare", path))

        @contextlib.contextmanager
        def hold() -> Iterator[None]:
            order.append(("hold",))
            yield

        self.vlm.prepare_lora = prepare_lora  # type: ignore[attr-defined]
        self.vlm.hold = hold  # type: ignore[attr-defined]
        self.roomy()
        started = await self.start(
            self.random_mode(batch_size=1, count_per_batch=1, writer=self.wid)
        )
        await self.wait(started.batch_id)
        self.assertEqual(order[:2], [("prepare", self.gguf), ("hold",)])
        batch = self.runner._batches[started.batch_id]
        self.assertEqual(batch.adapter_base, BASE)

    async def test_a_random_batch_whose_adapter_cannot_load_is_refused(self) -> None:
        # Nothing has rendered yet: the batch is refused up front (409
        # adapter_loading), never started to wait out every step.
        assert self.vlm is not None

        async def prepare_lora(path: Path, wait_s: Optional[float] = None) -> None:
            raise AdapterLoadingError("busy")

        self.vlm.prepare_lora = prepare_lora  # type: ignore[attr-defined]
        loop = asyncio.get_running_loop()
        began = loop.time()
        with self.assertRaises(WriterAdapterUnavailable) as caught:
            await self.start(
                self.random_mode(batch_size=2, count_per_batch=1, writer=self.wid)
            )
        self.assertLess(loop.time() - began, 1.0)
        self.assertEqual(caught.exception.code, "adapter_loading")
        self.assertEqual(self.runner._batches, {})
        self.assertEqual(self.comfy.submits, [])
        self.assertEqual(self.vlm.calls, [])


# ---- I2V lineage --------------------------------------------------------------


class TestI2vAdapterLineage(_I2vBase):
    def setUp(self) -> None:
        super().setUp()
        # TestFormStateAtIngest.setUp: a job-creating ComfyUI and a clip.
        self.comfy = i2v_tests._JobCreatingComfy(self.db)
        self.runner.comfy = self.comfy
        self.out = Path(self.tmp.name) / "clip.mp4"
        self.out.write_bytes(b"fake")
        self.db.save_media(
            i2v_tests.Media(
                file_path=self.out,
                file_size=4,
                width=640,
                height=944,
                format="mp4",
                created_at=i2v_tests.datetime.now(),
                modified_at=i2v_tests.datetime.now(),
            )
        )
        self.adapter = self.db.create_writer_adapter(
            name="i2v-v1", scope="all", kind="i2v", base_model=BASE, status="ready"
        )
        self.wid = adapter_writer_id(self.adapter)
        self.gguf = Path(self.tmp.name) / "adapter.gguf"
        self.runner.writer_adapter = {self.adapter: self.gguf}.get

    def _row(self, job_id: int) -> Dict[str, Any]:
        self._ingest(job_id)
        rows = self.db.list_i2v_videos(str(self.src))
        self.assertEqual(len(rows), 1)
        return dict(rows[0])

    def test_write_prompt_with_an_adapter_and_its_lineage(self) -> None:
        text, _warnings, model = self.run_async(
            self.runner.write_prompt(str(self.src), "a cat", 6, writer=self.wid)
        )
        self.assertEqual(model, BASE)
        self.assertEqual(self.vlm.calls[-1]["lora_path"], self.gguf)
        job_id = self._generate(
            prompt=text, writer_id=self.wid, draft_prompt=text, writer_model=model
        )
        row = self._row(job_id)
        self.assertEqual(row["writer_id"], self.wid)
        self.assertEqual(row["writer_model"], BASE)

    def test_base_writes_carry_no_adapter(self) -> None:
        self.run_async(self.runner.write_prompt(str(self.src), "a cat", 6))
        self.assertNotIn("lora_path", self.vlm.calls[-1])

    def test_generate_with_a_gone_adapters_draft_is_unavailable(self) -> None:
        self.db.soft_delete_writer_adapter(self.adapter)
        with self.assertRaises(WriterAdapterUnavailable):
            self._generate(
                writer_id=self.wid, draft_prompt="a draft", writer_model=BASE
            )
        self.assertEqual(self.comfy.submits, [])

    def test_a_t2i_adapter_is_unavailable_for_i2v(self) -> None:
        t2i = self.db.create_writer_adapter(
            name="t2i-v1", scope="all", kind="t2i", base_model=BASE, status="ready"
        )
        with self.assertRaises(WriterAdapterUnavailable):
            self.run_async(
                self.runner.write_prompt(
                    str(self.src), "a cat", 6, writer=adapter_writer_id(t2i)
                )
            )
        self.assertEqual(self.vlm.calls, [])


# ---- routes -----------------------------------------------------------------------


import tests.test_t2i_api as t2i_api_tests  # noqa: E402


class TestT2iRoutes(t2i_api_tests.T2iApiCase):
    BODY = {"caption": "a red kite", "seed": 1, "model": "krea2"}

    def test_writer_accepts_adapter_ids_only(self) -> None:
        ok = self.client.post(
            "/api/t2i/prompt", json={**self.BODY, "writer": "adapter:3"}
        )
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(self.runner.calls[-1][1]["writer"], "adapter:3")
        for bad in ("adapter:x", "adapter:0", "nope", "manual"):
            resp = self.client.post(
                "/api/t2i/prompt", json={**self.BODY, "writer": bad}
            )
            self.assertEqual(resp.status_code, 422, bad)

    def test_an_unavailable_adapter_is_a_409(self) -> None:
        self.runner.errors["generate_prompt"] = WriterAdapterUnavailable("adapter:3")
        resp = self.client.post(
            "/api/t2i/prompt", json={**self.BODY, "writer": "adapter:3"}
        )
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["detail"]["code"], "adapter_unavailable")

    def test_a_busy_writer_is_a_409_adapter_loading(self) -> None:
        self.runner.errors["generate_prompt"] = WriterAdapterUnavailable(
            "adapter:3", code="adapter_loading"
        )
        resp = self.client.post(
            "/api/t2i/prompt", json={**self.BODY, "writer": "adapter:3"}
        )
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["detail"]["code"], "adapter_loading")
        # The client needs to know WHICH adapter (the pick or a held draft).
        self.assertEqual(resp.json()["detail"]["writer_id"], "adapter:3")


def test_i2v_route_passes_the_writer_and_409s(tmp_path: Path) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api import i2v as i2v_api

    calls: List[Dict[str, Any]] = []

    class Runner:
        error: Optional[BaseException] = None

        async def write_prompt(self, *args: Any, **kw: Any) -> Any:
            calls.append(kw)
            if self.error is not None:
                raise self.error
            return "prompt", [], BASE

        async def start_batch(self, request: Any) -> Any:
            raise WriterAdapterUnavailable(str(request.writer_id))

    runner = Runner()
    i2v_api.set_i2v_runner(runner)
    try:
        app = FastAPI()
        app.include_router(i2v_api.router)
        client = TestClient(app)
        body = {"source_path": "/x.png", "duration_s": 6, "writer": "adapter:4"}
        resp = client.post("/api/i2v/prompt", json=body)
        assert resp.status_code == 200
        assert resp.json()["writer_id"] == "adapter:4"
        assert calls[-1]["writer"] == "adapter:4"
        bad = client.post("/api/i2v/prompt", json={**body, "writer": "base+taste"})
        assert bad.status_code == 422
        runner.error = WriterAdapterUnavailable("adapter:4")
        resp = client.post("/api/i2v/prompt", json=body)
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "adapter_unavailable"
        # A run started with a gone adapter's draft: 409 too (the fallback path).
        cfg = {"fast_preset_id": 1, "quality_preset_id": 2}
        with mock.patch(
            "backend.api.i2v.get_i2v_config",
            return_value={**i2v_api.get_i2v_config({}), **cfg},
        ), mock.patch("backend.api.i2v.load_app_config", return_value={}):
            resp = client.post(
                "/api/i2v/batches",
                json={
                    "source_path": "/x.png",
                    "prompt": "p",
                    "duration_s": 6,
                    "quality": "fast",
                    "seed": 1,
                    "seed_policy": "fixed",
                    "batch_size": 1,
                    "count_per_batch": 1,
                    "writer_id": "adapter:4",
                    "draft_prompt": "d",
                    "writer_model": BASE,
                },
            )
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "adapter_unavailable"
    finally:
        i2v_api.set_i2v_runner(None)


@pytest.mark.parametrize("released", [True, False])
def test_delete_withdraws_the_adapter_before_removing_its_files(
    tmp_path: Path, monkeypatch: Any, released: bool
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import backend.dependencies as deps
    from backend.api import writers

    monkeypatch.setenv("METASCAN_API_KEY", "")
    db = DatabaseManager(tmp_path)
    old = deps._db_singleton
    deps._db_singleton = db  # type: ignore[attr-defined]
    order: List[str] = []
    monkeypatch.setattr(writers.ws_manager, "broadcast_sync", lambda *a: None)

    async def withdraw(adapter_id: int) -> bool:
        order.append(f"withdraw {adapter_id}")
        return released  # False: a running server still maps its GGUF

    async def refresh() -> None:
        order.append("refresh")

    real_rmtree = writers.shutil.rmtree

    def rmtree(path: Any, *a: Any, **kw: Any) -> None:
        order.append("rmtree")
        real_rmtree(path, *a, **kw)

    try:
        with mock.patch(
            "backend.api.vlm.withdraw_writer_adapter", withdraw
        ), mock.patch("backend.api.vlm.refresh_writer_adapters", refresh), mock.patch(
            "backend.api.writers.shutil.rmtree", rmtree
        ), mock.patch(
            "backend.api.writers.get_data_dir", return_value=tmp_path
        ):
            a = db.create_writer_adapter(
                name="one", scope="all", kind="t2i", base_model=BASE, status="ready"
            )
            _gguf(tmp_path, a)
            app = FastAPI()
            app.include_router(writers.router)
            resp = TestClient(app).delete(f"/api/writers/adapters/{a}")
        if released:
            assert resp.json() == {"status": "deleted"}
            assert order == [f"withdraw {a}", "rmtree", "refresh"]
        else:
            # Never rely on the file removal failing: answer busy at once.
            assert resp.status_code == 409
            assert resp.json()["detail"]["code"] == "adapter_files_busy"
            assert order == [f"withdraw {a}"]
            assert db.get_writer_adapter(a)["status"] == "ready"
            assert (adapter_dir(tmp_path, a) / ADAPTER_GGUF).exists()
    finally:
        deps._db_singleton = old  # type: ignore[attr-defined]
        db.close()
