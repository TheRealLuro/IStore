"""Regression test for CR-7 + F17 — Dockerfile non-root + multi-stage.

Before this patch:
  - No `USER` directive → uvicorn ran as root inside the container.
    Combined with the compose layer's bind-mounts of `./backend`,
    `./migrations`, `./alembic.ini`, `./policies`, and the host HF
    cache, any in-container RCE-shaped bug rewrote source on the
    host and poisoned the model cache for the next boot.
  - The runtime image carried `build-essential` and `git`, giving
    any attacker who shelled into the container a compiler and
    SCM to develop further exploits in-place.

This file pins the two hardening properties so a future PR that
reverts `USER neuthek` or re-installs the compilers in the runtime
layer breaks at test collection rather than in production.

Running the actual `docker build` would catch more (e.g. that the
COPY paths still resolve), but the build is multi-GB and minutes
long; static parsing of the Dockerfile is the cheap test that fires
on every commit.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
_DOCKERFILE = _REPO_ROOT / "Dockerfile"


def _read_dockerfile() -> str:
    return _DOCKERFILE.read_text(encoding="utf-8")


def _split_stages(src: str) -> dict[str, str]:
    """Return {stage_name: stage_body} for every `FROM ... AS <name>`
    block. Comments and blank lines stay in the body so we can
    grep them too."""
    stages: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in src.splitlines(keepends=True):
        m = re.match(r"^\s*FROM\s+\S+\s+AS\s+(\S+)", line, re.IGNORECASE)
        if m:
            if current is not None:
                stages[current] = "".join(buf)
            current = m.group(1)
            buf = [line]
        elif current is not None:
            buf.append(line)
    if current is not None:
        stages[current] = "".join(buf)
    return stages


def test_dockerfile_uses_multi_stage_build() -> None:
    """At least two `FROM ... AS` stages must exist; the runtime
    stage cannot inherit the builder's compilers."""
    stages = _split_stages(_read_dockerfile())
    assert "builder" in stages, "Missing `builder` stage (F17)."
    assert "runtime" in stages, "Missing `runtime` stage."


def test_runtime_stage_drops_privileges() -> None:
    """The runtime stage must end with a `USER` directive that
    selects a non-root account. We require it to be exactly
    `USER neuthek` so the dev compose layer can rely on UID 1000."""
    stages = _split_stages(_read_dockerfile())
    runtime = stages["runtime"]
    matches = re.findall(r"^\s*USER\s+(\S+)\s*$", runtime, re.MULTILINE)
    assert matches, (
        "Runtime stage has no `USER` directive — the container would "
        "fall back to root. Add `USER neuthek` near the end of the "
        "runtime stage to keep uvicorn unprivileged."
    )
    assert matches[-1] == "neuthek", (
        f"Last USER directive in runtime is {matches[-1]!r}; expected "
        "`neuthek`. The CR-7 fix relies on a stable UID/username so "
        "bind-mount permissions on the host stay consistent."
    )
    assert matches[-1] != "root", "USER must not be `root`."


def test_runtime_stage_does_not_install_compilers() -> None:
    """Builder stage may have `build-essential`/`git`; runtime must
    not. A regression that reverts to a single-stage Dockerfile
    would re-introduce both and silently widen the attack surface."""
    stages = _split_stages(_read_dockerfile())
    runtime = stages["runtime"]
    # Strip comments so a doc reference to "build-essential" in a
    # comment doesn't break the test.
    runtime_code = "\n".join(
        line for line in runtime.splitlines() if not line.lstrip().startswith("#")
    )
    forbidden = ["build-essential", " git ", " gcc ", "git\\", "g++"]
    found = [tok for tok in forbidden if tok in runtime_code]
    assert not found, (
        f"Runtime stage installs build tools: {found}. Move them to "
        "the `builder` stage so the runtime image stays minimal."
    )


def test_builder_stage_is_not_marked_as_runtime() -> None:
    """A misnamed final stage (e.g. `FROM python:3.12 AS builder`
    listed last) would result in `docker build` selecting the
    builder stage by default. The test confirms the runtime stage
    is the final stage."""
    stages = _split_stages(_read_dockerfile())
    src = _read_dockerfile()
    last_from = None
    for m in re.finditer(r"^\s*FROM\s+\S+\s+AS\s+(\S+)", src, re.MULTILINE | re.IGNORECASE):
        last_from = m.group(1)
    assert last_from == "runtime", (
        f"Last `FROM ... AS` stage is {last_from!r}; must be "
        "`runtime` so `docker build` defaults to the hardened stage."
    )


def test_user_directive_comes_after_root_setup() -> None:
    """`apt-get install`, `useradd`, and `chown` need root. The
    `USER` switch has to happen AFTER all of them, not before."""
    src = _read_dockerfile()
    runtime_start = next(
        (
            m.start()
            for m in re.finditer(
                r"^\s*FROM\s+\S+\s+AS\s+runtime", src, re.MULTILINE | re.IGNORECASE
            )
        ),
        None,
    )
    assert runtime_start is not None
    runtime_src = src[runtime_start:]
    user_pos = runtime_src.find("USER neuthek")
    useradd_pos = runtime_src.find("useradd")
    apt_pos = runtime_src.find("apt-get install")
    assert user_pos > 0
    assert user_pos > useradd_pos, "USER comes before `useradd` — user does not exist yet."
    assert user_pos > apt_pos, (
        "USER comes before apt-get install — install would fail when "
        "running as non-root."
    )


def test_models_dir_is_pre_created_with_correct_owner() -> None:
    """HuggingFace cache lands at /models (per docker-compose.yml's
    HF_HOME). The runtime needs /models to exist + be writable by
    `neuthek` before USER drops privileges, otherwise transformers'
    first download fails with EACCES."""
    src = _read_dockerfile()
    assert "mkdir -p /models" in src, (
        "Pre-create /models in the runtime stage so HuggingFace can "
        "fall back to a named volume when the host bind mount is "
        "absent."
    )
    assert "chown neuthek" in src, (
        "/models needs to be owned by neuthek; otherwise the cache "
        "write fails after USER drops to neuthek."
    )


def test_venv_copied_from_builder_stage() -> None:
    """The runtime must not run its own pip install — that would
    require the compilers we removed. Confirm the venv is brought
    over from the builder via COPY --from."""
    src = _read_dockerfile()
    assert re.search(
        r"COPY\s+--from=builder\s+/opt/venv\s+/opt/venv", src
    ), (
        "Runtime stage does not COPY --from=builder /opt/venv ... — "
        "without it, runtime would need pip + the build deps, "
        "defeating the multi-stage split."
    )
    assert 'PATH="/opt/venv/bin:$PATH"' in src or "PATH=/opt/venv/bin:" in src, (
        "Runtime PATH must include /opt/venv/bin so `uvicorn` / "
        "`alembic` resolve to the venv binaries."
    )
