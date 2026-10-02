"""Classify the t2i caption CSV with a local llama-server.

Usage:
    python -m scripts.caption_classifier.classify [--csv PATH] [--out DIR]
        [--model qwen3vl-30b-a3b] [--parallel 16] [--ctx-per-slot 6144]
        [--count N | --sample N [--seed S]] [--new-run]
        [--server-url URL] [--log-file PATH]

--count N classifies only the first N rows (for trying the classifier out);
rows already classified with the current prompt version are skipped, so a
later full run does not redo them. --server-url uses an already running
llama-server instead of starting one.

The captions CSV is only read. Results go to <out>/results-<version>.jsonl;
run summarize.py to produce classifications.csv.

Exit codes: 0 every selected row ok; 1 finished with error rows; 2 unusable
arguments or input; 3 llama-server failed; 130 interrupted.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Union

from metascan.core.t2i_captions import CaptionStore
from metascan.utils.log_files import rotating_file_handler

from .prompt import PROMPT_VERSION
from .results import (
    ResultsWriter,
    VersionConflict,
    caption_sha1,
    load_done,
    open_run,
    select_rows,
)
from .runner import FatalServerError, Runner
from .server import LlamaServer, ServerError, build_command

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CSV = REPO_ROOT / "data" / "t2i_captions" / "t2i_captions.csv"
DEFAULT_OUT = REPO_ROOT / "data" / "t2i_captions" / "classifier"
DEFAULT_LOG = REPO_ROOT / "logs" / "caption_classifier.log"

logger = logging.getLogger("caption_classifier")


class ExternalServer:
    """A llama-server someone else started; it is never restarted or stopped."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def alive(self) -> bool:
        return True

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def restart(self) -> None:
        raise FatalServerError(f"external server {self.base_url} cannot be restarted")


def setup_logging(log_file: Path) -> None:
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
    logger.addHandler(console)
    file_handler = rotating_file_handler(log_file)
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)


def parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--model", default="qwen3vl-30b-a3b")
    ap.add_argument("--parallel", type=int, default=16)
    ap.add_argument("--ctx-per-slot", type=int, default=6144)
    pick = ap.add_mutually_exclusive_group()
    pick.add_argument("--count", type=int, help="classify only the first N rows")
    pick.add_argument("--sample", type=int, help="classify N random rows")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--new-run", action="store_true")
    ap.add_argument("--server-url", default="")
    ap.add_argument("--log-file", type=Path, default=DEFAULT_LOG)
    return ap.parse_args(argv)


async def _run(
    args: argparse.Namespace, store: CaptionStore, row_ids: List[int], path: Path
) -> int:
    server: Union[ExternalServer, LlamaServer]
    if args.server_url:
        server = ExternalServer(args.server_url)
    else:
        server = LlamaServer(
            lambda port: build_command(
                args.model, port, args.parallel, args.ctx_per_slot
            )
        )
    writer = ResultsWriter(path)
    runner = Runner(
        rows=store,
        server=server,
        writer=writer,
        model_id=args.model,
        prompt_version=PROMPT_VERSION,
        workers=args.parallel,
    )
    loop = asyncio.get_running_loop()
    main_task = asyncio.current_task()
    presses = 0

    def on_sigint() -> None:
        nonlocal presses
        presses += 1
        if presses == 1:
            logger.warning(
                "stopping after the captions in flight; Ctrl-C again to abort"
            )
            runner.request_stop()
        elif main_task is not None:
            main_task.cancel()

    try:
        loop.add_signal_handler(signal.SIGINT, on_sigint)
    except (NotImplementedError, RuntimeError):
        pass
    try:
        await server.start()
        stats = await runner.run(row_ids)
    except (ServerError, FatalServerError) as exc:
        logger.error("%s", exc)
        return 3
    except asyncio.CancelledError:
        logger.warning("aborted; rows written so far are kept")
        return 130
    finally:
        try:
            loop.remove_signal_handler(signal.SIGINT)
        except (NotImplementedError, RuntimeError):
            pass
        writer.close()
        await server.stop()
    logger.info("finished: %d ok, %d errors → %s", stats.ok, stats.errors, path)
    if stats.stopped:
        return 130
    return 0 if stats.errors == 0 else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    setup_logging(args.log_file)
    store = CaptionStore(args.csv)
    if not store.available():
        logger.error("cannot use %s: %s", args.csv, store.error())
        return 2
    try:
        path = open_run(args.out, PROMPT_VERSION, args.new_run)
    except VersionConflict as exc:
        logger.error("%s", exc)
        return 2
    row_ids = select_rows(store.total(), args.count, args.sample, args.seed)
    done = load_done(path)
    pending = [
        r for r in row_ids if (r, caption_sha1(store.get(r).caption)) not in done
    ]
    logger.info(
        "prompt version %s · %d selected · %d already done · %d to classify → %s",
        PROMPT_VERSION,
        len(row_ids),
        len(row_ids) - len(pending),
        len(pending),
        path,
    )
    if not pending:
        return 0
    return asyncio.run(_run(args, store, pending, path))


if __name__ == "__main__":
    sys.exit(main())
