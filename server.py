'''Written by Cael Shoop.'''

import os
import json
import asyncio
from dotenv import load_dotenv

load_dotenv()


PORT = int(os.getenv("PORT"))
HOST = os.getenv("HOST")


class Server:
    def __init__(self, host=HOST, port=PORT):
        self.host = host
        self.port = port
        self.callback = None

    async def handle_client(self, reader, writer):
        while True:
            data = await reader.read(99999)
            if not data:
                break
            message = data.decode()

            try:
                data_dict = json.loads(message)
                if self.callback:
                    if asyncio.iscoroutinefunction(self.callback):
                        await self.callback(data_dict)
                    else:
                        self.callback(data_dict)
                response = "valid"
            except json.JSONDecodeError:
                response = "invalid JSON"

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
