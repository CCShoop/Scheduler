import asyncio
import json

import pytest

from server import Server, json_incomplete
from fakes import run


async def exchange(server: Server, *chunks: "str | bytes") -> str:
    """
    Sends chunks to the server's client handler over a real local socket and returns everything it replied.
    Responses aren't delimited, so several of them arrive concatenated.
    """
    listener = await asyncio.start_server(server.handle_client, "127.0.0.1", 0)
    port = listener.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    for chunk in chunks:
        writer.write(chunk if isinstance(chunk, bytes) else chunk.encode())
        await writer.drain()
        # Give the server a chance to read each chunk on its own
        await asyncio.sleep(0.05)
    writer.write_eof()
    response = (await asyncio.wait_for(reader.read(), timeout=2)).decode()
    writer.close()
    await writer.wait_closed()
    listener.close()
    await listener.wait_closed()
    return response


def test_valid_json_calls_async_callback():
    received = []

    async def callback(data):
        received.append(data)

    server = Server(host="127.0.0.1", port=0)
    server.callback = callback
    assert run(exchange(server, json.dumps({"name": "test"}))) == "valid"
    assert received == [{"name": "test"}]


def test_valid_json_calls_sync_callback():
    received = []
    server = Server(host="127.0.0.1", port=0)
    server.callback = received.append
    assert run(exchange(server, json.dumps({"name": "test"}))) == "valid"
    assert received == [{"name": "test"}]


def test_message_split_between_values_is_reassembled():
    received = []
    server = Server(host="127.0.0.1", port=0)
    server.callback = received.append
    assert run(exchange(server, '{"name": ', '"split"}')) == "valid"
    assert received == [{"name": "split"}]


def test_incomplete_message_at_disconnect_gets_no_response():
    server = Server(host="127.0.0.1", port=0)
    assert run(exchange(server, '{"name": ')) == ""


def test_message_split_inside_a_string_is_reassembled():
    received = []
    server = Server(host="127.0.0.1", port=0)
    server.callback = received.append
    message = json.dumps({"name": "split", "usernames": "a, b, c"})
    middle = len(message) // 2
    assert run(exchange(server, message[:middle], message[middle:])) == "valid"
    assert received == [{"name": "split", "usernames": "a, b, c"}]


def test_invalid_json_is_rejected_and_buffer_reset():
    received = []
    server = Server(host="127.0.0.1", port=0)
    server.callback = received.append
    assert run(exchange(server, "not json", json.dumps({"name": "after"}))) == "invalid JSONvalid"
    assert received == [{"name": "after"}]


def test_callback_error_is_reported():
    async def callback(data):
        raise ValueError("no guild")

    server = Server(host="127.0.0.1", port=0)
    server.callback = callback
    assert run(exchange(server, json.dumps({"name": "test"}))) == "error: no guild"


def test_no_callback_still_acknowledges():
    server = Server(host="127.0.0.1", port=0)
    assert run(exchange(server, json.dumps({"name": "test"}))) == "valid"


@pytest.mark.parametrize("message, cut_after", [
    ('{"a": true}', '{"a": tru'),          # partway through a literal
    ('{"a": 1.5}', '{"a": 1.'),            # partway through a number
    ('{"a": -1}', '{"a": -'),
    ('{"a": "x\\""}', '{"a": "x\\'),      # right after an escape character
    ('{"a": "\\u0041"}', '{"a": "\\u00'),  # partway through a unicode escape
    ('{"a": [1, 2]}', '{"a": [1, 2'),      # inside a nested array
])
def test_message_split_anywhere_is_reassembled(message, cut_after):
    received = []
    server = Server(host="127.0.0.1", port=0)
    server.callback = received.append
    assert run(exchange(server, cut_after, message[len(cut_after):])) == "valid"
    assert received == [json.loads(message)]


@pytest.mark.parametrize("text", ["café", "🎮 night", "日本"])
def test_message_split_inside_a_character_is_reassembled(text):
    received = []
    server = Server(host="127.0.0.1", port=0)
    server.callback = received.append
    message = json.dumps({"name": text}, ensure_ascii=False).encode()
    # Cut after the first byte of the first multi-byte character
    cut = next(i for i, byte in enumerate(message) if byte >= 0x80) + 1
    assert run(exchange(server, message[:cut], message[cut:])) == "valid"
    assert received == [{"name": text}]


def test_invalid_utf8_is_rejected_and_buffer_reset():
    received = []
    server = Server(host="127.0.0.1", port=0)
    server.callback = received.append
    assert run(exchange(server, b'{"name": "\xff"}', json.dumps({"name": "after"}))) == "invalid JSONvalid"
    assert received == [{"name": "after"}]


@pytest.mark.parametrize("buffer, expected", [
    ('{"a": 1}', False),
    ('{"a": 1', True),
    ('{"a": "}"', True),           # brace inside a string doesn't close the object
    ('{"a": "\\"}"', True),      # escaped quote doesn't end the string
    ('{"a": "\\\\"}', False),   # escaped backslash then a real closing quote
    ('[{"a": [1]}, {"b": 2}]', False),
    ('"unterminated', True),
    ('not json', False),
    ('{"a": 1}}', False),           # too many closers is invalid, not incomplete
])
def test_json_incomplete(buffer, expected):
    assert json_incomplete(buffer) is expected


def test_whitespace_only_waits_for_a_message():
    received = []
    server = Server(host="127.0.0.1", port=0)
    server.callback = received.append
    assert run(exchange(server, "  \n", json.dumps({"name": "test"}))) == "valid"
    assert received == [{"name": "test"}]
