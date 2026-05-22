"""Port allocation invariant; documents subprocess-crash coverage gap."""

from metascan.core.vlm_client import _free_port


def test_each_call_returns_a_unique_port():
    a = _free_port()
    b = _free_port()
    assert a != b
    assert isinstance(a, int) and 1024 < a < 65536


# Note: real-binary subprocess-crash recovery (the _wait_exit auto-respawn
# path) is exercised by manual integration testing during Phase 5/6, not
# unit tests — the fake-server fixture uses spawn_override, which bypasses
# the subprocess we'd need to crash.
