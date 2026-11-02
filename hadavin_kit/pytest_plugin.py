"""Pytest plugin: any test that opens a network socket or runs a network tool fails.

Enable with `pytest_plugins = ["hadavin_kit.pytest_plugin"]` in the repo-root conftest. The patch is installed at
session start (before any session or module fixture) and removed at session end. Blocked: connect, connect_ex,
sendto, sendmsg, bind, listen, accept (on every socket), name resolution (getaddrinfo, gethostbyname[_ex],
gethostbyaddr, getnameinfo, getfqdn, create_connection, create_server, fromfd), and subprocess launches of network
tools (curl, wget, nc, ncat, telnet, ssh, scp, ftp, git-remote helpers). `socket.socketpair()` stays allowed because
the standard asyncio event loop needs it (it is local only; on Windows it uses a loopback pair created inside the
call). Anything else, including an event loop connecting out, fails.
"""

from __future__ import annotations

import os
import socket
import subprocess
import threading

_ALLOW = threading.local()
_ORIGINAL: dict[tuple[object, str], object] = {}
NETWORK_TOOLS = {
    "curl",
    "wget",
    "nc",
    "ncat",
    "netcat",
    "telnet",
    "ssh",
    "scp",
    "sftp",
    "ftp",
    "git-remote-https",
}


class NetworkBlocked(RuntimeError):
    pass


def _blocked(*_a, **_k):
    raise NetworkBlocked("network access attempted in a test (tests run offline)")


def _gate(name: str, fn):
    def wrapper(*a, **k):
        if getattr(_ALLOW, "on", False):
            return fn(*a, **k)
        return _blocked()

    wrapper.__name__ = name
    return wrapper


def _socketpair(*a, **k):
    original = _ORIGINAL[(socket, "socketpair")]
    _ALLOW.on = True
    try:
        return original(*a, **k)
    finally:
        _ALLOW.on = False


def _popen_init(original):
    def init(self, args, *a, **k):
        first = args if isinstance(args, str | bytes | os.PathLike) else (args[0] if args else "")
        name = os.path.basename(os.fsdecode(first).strip().split(" ")[0]).lower()
        if name.removesuffix(".exe") in NETWORK_TOOLS:
            _blocked()
        return original(self, args, *a, **k)

    return init


def install() -> None:
    if _ORIGINAL:
        return
    targets: list[tuple[object, str]] = [
        (socket.socket, n)
        for n in ("connect", "connect_ex", "sendto", "sendmsg", "bind", "listen", "accept")
    ]
    targets += [
        (socket, n)
        for n in (
            "getaddrinfo",
            "gethostbyname",
            "gethostbyname_ex",
            "gethostbyaddr",
            "getnameinfo",
            "getfqdn",
            "create_connection",
            "create_server",
            "fromfd",
        )
    ]
    targets = [
        (o, n) for o, n in targets if hasattr(o, n)
    ]  # sendmsg and fromfd are not on every platform
    for owner, name in targets:
        _ORIGINAL[(owner, name)] = getattr(owner, name)
        setattr(owner, name, _blocked)
    _ORIGINAL[(socket, "socketpair")] = socket.socketpair
    socket.socketpair = _socketpair
    # socketpair on Windows runs bind/listen/connect inside the call: let those through only while it runs
    for owner, name in [
        (socket.socket, n)
        for n in ("connect", "bind", "listen", "accept")
        if (socket.socket, n) in _ORIGINAL
    ]:
        setattr(owner, name, _gate(name, _ORIGINAL[(owner, name)]))
    _ORIGINAL[(subprocess.Popen, "__init__")] = subprocess.Popen.__init__
    subprocess.Popen.__init__ = _popen_init(subprocess.Popen.__init__)


def uninstall() -> None:
    for (owner, name), original in list(_ORIGINAL.items()):
        setattr(owner, name, original)
    _ORIGINAL.clear()


def pytest_sessionstart(session) -> None:
    install()


def pytest_sessionfinish(session, exitstatus) -> None:
    uninstall()
