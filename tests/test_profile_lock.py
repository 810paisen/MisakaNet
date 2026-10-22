#!/usr/bin/env python3
"""The profile lock must be a real lock on every platform (#1775).

`_save` swaps profile.json with `os.replace`, which prevents *torn* writes and does nothing for
*lost updates*. The exclusive lock is what prevents the lost update — and the first version of
that lock was POSIX-only: on Windows `import fcntl` raised, the code took its "best-effort"
branch, and the read-modify-write ran with **no lock at all**. The CI leg `windows-latest, 3.12`
measured the consequence: 40 concurrent increments, 39 survived.

This machine is POSIX, so the Windows branch cannot be *executed* here. It is still checked: the
platform-specific primitive sits behind a seam (`_windows_primitives()` / `_windows_backend()`) and is
exercised below with a stand-in for the Windows CRT module that reproduces the one behaviour the
implementation depends on — `LK_NBLCK` reserves a byte range for the calling handle and fails
immediately when another handle already holds it.

Every assertion in this file is falsifiable. The mutations that turn them red are recorded in
the commit messages that added them.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from misakanet import profile as profile_mod  # noqa: E402


class FakeMsvcrt:
    """A stand-in for the Windows CRT module (`msvcrt`).

    It reproduces the behaviour the lock implementation depends on:

    * `locking(fd, LK_NBLCK, 1)` reserves one byte for the calling descriptor and fails
      immediately (`OSError`) when another descriptor already holds the range;
    * `locking(fd, LK_UNLCK, 1)` gives the range back.

    `calls` records every call together with the file offset it was made at, so a test can tell
    that the implementation reserves and releases the *same* range.
    """

    LK_NBLCK = 1
    LK_UNLCK = 2

    def __init__(self) -> None:
        self.held: set[int] = set()
        self.calls: list[tuple[int, int, int]] = []

    def locking(self, fd: int, mode: int, nbytes: int) -> None:
        offset = os.lseek(fd, 0, os.SEEK_CUR)
        self.calls.append((mode, nbytes, offset))
        if mode == self.LK_NBLCK:
            if self.held and fd not in self.held:
                raise OSError(13, "Permission denied")
            self.held.add(fd)
            return
        if mode == self.LK_UNLCK:
            self.held.discard(fd)
            return
        raise AssertionError(f"unexpected locking mode {mode}")


@pytest.fixture
def fake_msvcrt(monkeypatch) -> FakeMsvcrt:
    """Put the Windows CRT stand-in behind the module's import seam.

    The seam hands over both Windows-only primitives — the `msvcrt` module and the
    `os.O_BINARY` flag — so a test that simulates Windows on another platform replaces the pair
    in one move, and the module never has to guess the flag with `getattr`.
    """
    fake = FakeMsvcrt()
    monkeypatch.setattr(profile_mod, "_windows_primitives", lambda: (fake, 0))
    return fake


@pytest.fixture
def windows_platform(monkeypatch) -> None:
    """Make the module take its Windows branch, on this machine."""
    monkeypatch.setattr(sys, "platform", "win32")


def test_the_running_platform_always_gets_a_real_lock(tmp_path):
    """Whatever the platform, the lock must be a real primitive — never a no-op.

    The old code had a third outcome: "no `fcntl` here, carry on without a lock".
    """
    backend = profile_mod._select_lock_backend(tmp_path / ".profile.lock")
    assert backend.name in {"fcntl.flock", "msvcrt.locking"}, backend.name


def test_a_platform_without_a_lock_primitive_fails_loudly(monkeypatch, tmp_path):
    """A platform with neither primitive must get an explicit error, not an unprotected write."""
    monkeypatch.setattr(sys, "platform", "sunos5")
    monkeypatch.setitem(sys.modules, "fcntl", None)  # `import fcntl` now raises ImportError
    monkeypatch.setattr(profile_mod, "PROFILE_PATH", tmp_path / "profile.json")

    entered = False
    with pytest.raises(profile_mod.ProfileLockError):
        with profile_mod._profile_lock():
            entered = True
    assert entered is False, "the profile must not be written when no lock can be taken"


def test_the_windows_branch_reserves_and_releases_the_same_byte(
    fake_msvcrt, windows_platform, tmp_path
):
    """`msvcrt.locking` is used, on byte 0, and the byte is given back."""
    lock_path = tmp_path / ".profile.lock"
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        backend = profile_mod._select_lock_backend(lock_path)
        assert backend.name == "msvcrt.locking", backend.name
        backend.acquire(fd)
        backend.release(fd)
    finally:
        os.close(fd)

    assert fake_msvcrt.calls[0][:2] == (FakeMsvcrt.LK_NBLCK, 1), fake_msvcrt.calls
    assert fake_msvcrt.calls[-1][:2] == (FakeMsvcrt.LK_UNLCK, 1), fake_msvcrt.calls
    assert [offset for _, _, offset in fake_msvcrt.calls] == [0, 0], (
        "the reserved range and the released range must be the same one"
    )


def test_the_windows_branch_excludes_a_second_holder(
    fake_msvcrt, windows_platform, monkeypatch, tmp_path
):
    """A second holder is excluded instead of being let in next to the first one."""
    monkeypatch.setattr(profile_mod, "_LOCK_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(profile_mod, "_LOCK_POLL_SECONDS", 0.01)
    lock_path = tmp_path / ".profile.lock"
    backend = profile_mod._select_lock_backend(lock_path)
    first = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    second = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        backend.acquire(first)
        with pytest.raises(profile_mod.ProfileLockError):
            backend.acquire(second)
        backend.release(first)
        backend.acquire(second)  # the range is available again once it was given back
        backend.release(second)
    finally:
        os.close(first)
        os.close(second)


def test_the_windows_branch_fails_fast_when_the_lock_cannot_be_taken(
    monkeypatch, windows_platform, tmp_path
):
    """A lock that cannot be taken is an explicit error — never a silent unprotected write."""

    class Refuses(FakeMsvcrt):
        def locking(self, fd: int, mode: int, nbytes: int) -> None:
            super().locking(fd, mode, nbytes)
            raise OSError(13, "Permission denied")

    monkeypatch.setattr(profile_mod, "_windows_primitives", lambda: (Refuses(), 0))
    monkeypatch.setattr(profile_mod, "_LOCK_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(profile_mod, "_LOCK_POLL_SECONDS", 0.01)
    monkeypatch.setattr(profile_mod, "PROFILE_PATH", tmp_path / "profile.json")

    entered = False
    with pytest.raises(profile_mod.ProfileLockError):
        with profile_mod._profile_lock():
            entered = True
    assert entered is False, "the profile must not be written when the lock could not be taken"


def test_profile_lock_on_the_windows_branch_serialises_threads(
    fake_msvcrt, windows_platform, monkeypatch, tmp_path
):
    """Two threads must not be inside the critical section at the same time.

    This is the Windows counterpart of the 40-thread test in
    `tests/test_issue_1775_severe.py`, run against the Windows branch.
    """
    monkeypatch.setattr(profile_mod, "PROFILE_PATH", tmp_path / "profile.json")
    inside = 0
    entered_at = []
    barrier = threading.Barrier(2)

    def worker() -> None:
        nonlocal inside
        barrier.wait(timeout=10)
        with profile_mod._profile_lock():
            inside += 1
            entered_at.append(inside)
            time.sleep(0.05)
            inside -= 1

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert max(entered_at) == 1, f"the critical section was entered concurrently: {entered_at}"


def test_the_lock_is_released_when_the_body_raises(monkeypatch, tmp_path):
    """A crash inside the critical section must not leave the profile wedged.

    This is the property a lock file created with `O_CREAT|O_EXCL` cannot have, and the reason
    the lock is a lock *on a file* rather than a file that stands for the lock.
    """
    monkeypatch.setattr(profile_mod, "PROFILE_PATH", tmp_path / "profile.json")
    with pytest.raises(RuntimeError):
        with profile_mod._profile_lock():
            raise RuntimeError("boom")
    with profile_mod._profile_lock():
        pass


def test_a_backfill_cannot_erase_a_concurrent_increment(monkeypatch, tmp_path):
    """The read path writes too: `_load` falls back to `_backfill`, which saves a profile.

    That write took no lock at all, so a backfill could land after another caller's increment
    and erase it — the same lost update the lock exists to prevent, on the one path that had no
    lock to begin with.
    """
    profile_path = tmp_path / "profile.json"
    monkeypatch.setattr(profile_mod, "PROFILE_PATH", profile_path)

    reader_is_backfilling = threading.Event()
    increment_finished = threading.Event()

    def backfill() -> dict:
        if threading.current_thread() is reader:
            # The reader is about to write a profile of its own. Wait for the concurrent
            # increment to land: with the lock held the increment cannot land until the
            # backfill is done (so the wait times out), and without the lock it lands first
            # and this backfill would erase it.
            reader_is_backfilling.set()
            increment_finished.wait(timeout=2)
        profile = {
            "stage": "newcomer",
            "referral_code": "MPROBE002",
            "referred_by": "",
            "search_count": 0,
            "lesson_count": 0,
            "last_active": "2026-10-01T00:00:00+00:00",
        }
        profile_mod._save(profile)
        return profile

    monkeypatch.setattr(profile_mod, "_backfill", backfill)

    def read_stage() -> None:
        profile_mod.get_stage()

    def increment() -> None:
        reader_is_backfilling.wait(timeout=5)
        profile_mod.increment_search()
        increment_finished.set()

    reader = threading.Thread(target=read_stage)
    writer = threading.Thread(target=increment)
    reader.start()
    writer.start()
    reader.join(timeout=30)
    writer.join(timeout=30)

    saved = json.loads(profile_path.read_text(encoding="utf-8"))
    assert saved["search_count"] == 1, (
        f"the backfill erased the concurrent increment: {saved}"
    )
