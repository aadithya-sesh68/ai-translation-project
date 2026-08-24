"""Open and close a WebSocket through a reverse proxy for integration checks."""

from __future__ import annotations

import argparse
import asyncio

from websockets.asyncio.client import connect


async def probe(url: str, origin: str) -> None:
    async with connect(url, origin=origin):
        pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("origin")
    arguments = parser.parse_args()
    asyncio.run(probe(arguments.url, arguments.origin))
    print("WebSocket upgrade succeeded.")


if __name__ == "__main__":
    main()
