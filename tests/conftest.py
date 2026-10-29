"""Enforce the default suite's offline contract, including future tests."""

import socket
from urllib.request import OpenerDirector

import pytest


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Network forbidden in offline tests; inject a fake transport")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(OpenerDirector, "open", blocked)
