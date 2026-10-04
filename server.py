'''Written by Cael Shoop.'''

import os
import json
import asyncio
from dotenv import load_dotenv

load_dotenv()


PORT = int(os.getenv("PORT"))
HOST = os.getenv("HOST")


def json_incomplete(buffer: str) -> bool:
    """
    Indicates whether the buffer ends partway through a JSON object or array,
    meaning the rest of the message has not been received yet.
    """
    depth = 0
    in_string = False
    escaped = False
    for char in buffer:
        if in_string:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in '{[':
            depth += 1
        elif char in '}]':
            depth -= 1
    return in_string or depth > 0


class Server:
    def __init__(self, host=HOST, port=PORT):
        self.host = host
        self.port = port
        self.callback = None

    async def handle_client(self, reader, writer):
        buffer = ""
        while True:
            data = await reader.read(99999)
            if not data:
                break
            buffer += data.decode()

            # A message split across reads; wait for the rest
            if buffer.strip() == "" or json_incomplete(buffer):
                continue
            try:
                data_dict = json.loads(buffer)
            except json.JSONDecodeError:
                buffer = ""
                response = "invalid JSON"
            else:
                buffer = ""
                try:
                    if self.callback:
                        if asyncio.iscoroutinefunction(self.callback):
                            await self.callback(data_dict)
                        else:
                            self.callback(data_dict)
                    response = "valid"
                except Exception as e:
                    response = f"error: {e}"

            writer.write(response.encode())
            await writer.drain()

        writer.close()
        await writer.wait_closed()

    async def start_server(self):
        server = await asyncio.start_server(self.handle_client, self.host, self.port)
        async with server:
            await server.serve_forever()

    def run(self):
        asyncio.run(self.start_server())
