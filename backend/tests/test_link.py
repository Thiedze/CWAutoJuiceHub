"""NanoLink against the simulated Nano: connecting, matching replies, errors."""

import pytest

from cwjh.nano_link import NanoError, NanoLink, find_port
from cwjh.sim.nano_sim import NanoSim

from .conftest import wait_for

async def started(sim, **kwargs):
    link = NanoLink(sim.line(), ready_timeout_s=0.2, ping_s=10, retry_s=0.05, **kwargs)
    await link.start()
    await wait_for(lambda: link.online)
    return link


@pytest.mark.asyncio
async def test_ready_gives_firmware_and_bays():
    link = await started(NanoSim(bays=3, fw="0.2.0"))
    try:
        assert (link.fw, link.bays) == ("0.2.0", 3)
        assert await link.request("Q", 1) == ["STATE", "0", "0", "0", "0"]
        assert await link.request("G 2 5", 1) == ["OK", "5"]
    finally:
        link.stop()


@pytest.mark.asyncio
async def test_probe_when_the_nano_does_not_reset():
    sim = NanoSim(bays=2)
    sim.send_ready = False
    link = await started(sim)
    try:
        assert link.bays == 2
        assert sim.received[0] == "0 Q"
    finally:
        link.stop()


@pytest.mark.asyncio
async def test_errors_and_tap():
    lines = []
    link = await started(NanoSim(bays=1), tap=lambda d, line: lines.append((d, line)))
    try:
        with pytest.raises(NanoError, match="no_motor"):
            await link.request("G 2 10", 1)
        with pytest.raises(NanoError, match="invalid"):
            await link.request("X", 1)
        assert ("in", "READY sim 1") in lines
        assert any(d == "out" and line.endswith(" G 2 10") for d, line in lines)
    finally:
        link.stop()


@pytest.mark.asyncio
async def test_offline_request_fails_fast():
    link = NanoLink(NanoSim().line())
    with pytest.raises(NanoError, match="offline"):
        await link.request("Q", 1)


def test_find_port_passes_explicit_paths():
    assert find_port("/dev/ttyUSB7") == "/dev/ttyUSB7"
