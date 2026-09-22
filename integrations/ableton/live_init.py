"""Klody's independent instance of the version-pinned AbletonMCP bridge.

bridge.py is copied byte-for-byte from mcp-server-ableton-live==1.8.0.
Use a separate socket so Song2Chords' existing KlodyBridge keeps port 9877.
"""
from . import bridge

bridge.DEFAULT_PORT = 9878
bridge.HOST = "127.0.0.1"


def create_instance(c_instance):
    return bridge.create_instance(c_instance)
