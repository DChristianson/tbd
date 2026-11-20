"""Records a job keeps besides its results: its console output and the
toolchain that graded it (docs/design/execution.md, "Execution contract")."""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

from tbd.paths import RACER_CHECK
from tbd.tools.assembler import DASM_BIN

CONSOLE_LOG = "console.log"


def toolchain() -> dict:
    """What graded the run. Values that can't be determined are None, not guesses.

    image_digest comes from TBD_IMAGE_DIGEST, set by whoever runs the container
    (an image can't know its own digest at build time)."""
    return {"image_digest": os.environ.get("TBD_IMAGE_DIGEST") or None,
            "dasm": dasm_version(),
            "racer_check": {"path": str(RACER_CHECK), "sha256": file_sha256(RACER_CHECK)}}


def compute() -> dict:
    """The machine that served the model, as far as whoever started the job knows it.

    case0 can't tell this itself: on the Mac or in the dev container the model runs
    elsewhere, so a GPU seen locally would be the wrong one. The pod runner
    (tbd.eval.podrunner) runs Ollama beside case0, so it sets TBD_GPU (nvidia-smi's
    GPU name) and TBD_HOST_ID (the RunPod pod id, or the hostname). Unset is None."""
    return {"gpu": os.environ.get("TBD_GPU") or None,
            "host_id": os.environ.get("TBD_HOST_ID") or None}


def dasm_version() -> str | None:
    """e.g. "2.20.14.1", from the banner `dasm` prints when run with no arguments."""
    if shutil.which(DASM_BIN) is None:
        return None
    try:
        out = subprocess.run([DASM_BIN], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    first = out.splitlines()[0].split() if out.strip() else []
    return first[1] if len(first) >= 2 and first[0] == "DASM" else None


def file_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


class _Tee:
    def __init__(self, stream, log):
        self.stream, self.log = stream, log

    def write(self, s):
        self.log.write(s)
        return self.stream.write(s)

    def flush(self):
        self.log.flush()
        self.stream.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


@contextlib.contextmanager
def console_log(job_dir: Path):
    """Copy everything printed to stdout and stderr into job_dir/console.log,
    still printing it. One file, in the order it was printed. An exception
    leaving the block (an interrupt or a crash) has its traceback logged too."""
    with open(job_dir / CONSOLE_LOG, "a") as log:
        out, err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = _Tee(out, log), _Tee(err, log)
        try:
            yield
        except BaseException as e:
            log.write("".join(traceback.format_exception(e)))
            raise
        finally:
            sys.stdout, sys.stderr = out, err
