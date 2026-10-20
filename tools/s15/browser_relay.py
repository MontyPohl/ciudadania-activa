"""Puertos loopback hacia un único contenedor Docker interno validado.

Docker 29 omite el publish de redes internas. Este relay conserva esa red y no
añade egress: solo conecta API y forwarder firmado del mundo browser S15.
"""

import argparse
import asyncio
import ipaddress
import json
import subprocess
from functools import partial

CONTAINER = "ca-s15-browser-web"
NETWORK = "ca-s15-browser-local"
PORTS = {58115: 8000, 18515: 18517}
MAX_CONNECTIONS = 64
IDLE_TIMEOUT = 30


def validate_target(container, network):
    env = dict(item.split("=", 1) for item in container["Config"].get("Env", []) if "=" in item)
    if (
        container["Config"].get("Labels", {}).get("ciudadania.sprint") != "15"
        or env.get("S15_BROWSER_WORLD") != NETWORK
        or env.get("DJANGO_SETTINGS_MODULE") != "tools.s15.browser_settings"
        or network.get("Internal") is not True
        or network.get("Labels", {}).get("ciudadania.sprint") != "15"
        or set(container["NetworkSettings"]["Networks"]) != {NETWORK}
    ):
        raise ValueError(
            "El relay requiere el contenedor y red interna exclusivos de navegador S15."
        )
    target = container["NetworkSettings"]["Networks"][NETWORK]["IPAddress"]
    address = ipaddress.ip_address(target)
    ranges = [ipaddress.ip_network(row["Subnet"]) for row in network["IPAM"]["Config"]]
    if (
        address.version != 4
        or not address.is_private
        or address.is_loopback
        or address.is_link_local
        or not any(address in subnet for subnet in ranges)
    ):
        raise ValueError("El destino no pertenece a la red privada del navegador.")
    return target


async def pipe(reader, writer):
    try:
        while data := await asyncio.wait_for(reader.read(65536), timeout=IDLE_TIMEOUT):
            writer.write(data)
            await asyncio.wait_for(writer.drain(), timeout=5)
        if writer.can_write_eof():
            writer.write_eof()
    except (OSError, TimeoutError, asyncio.CancelledError):
        pass


async def relay(target, port, slots, reader, writer):
    upstream = None
    try:
        if slots.locked():
            return
        async with slots:
            other, upstream = await asyncio.wait_for(
                asyncio.open_connection(target, port), timeout=5
            )
            await asyncio.gather(pipe(reader, upstream), pipe(other, writer))
    except (OSError, TimeoutError):
        pass
    finally:
        for stream in (writer, upstream):
            if stream is not None:
                stream.close()
                try:
                    await asyncio.wait_for(stream.wait_closed(), timeout=5)
                except (OSError, TimeoutError):
                    pass


async def serve(target):
    slots = asyncio.Semaphore(MAX_CONNECTIONS)
    servers = [
        await asyncio.start_server(
            partial(relay, target, dest, slots), "127.0.0.1", source, backlog=16, limit=65536
        )
        for source, dest in PORTS.items()
    ]
    try:
        await asyncio.gather(*(server.serve_forever() for server in servers))
    finally:
        for server in servers:
            server.close()
        await asyncio.gather(*(server.wait_closed() for server in servers))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    container = json.loads(subprocess.check_output(["docker", "inspect", CONTAINER]))[0]
    network = json.loads(subprocess.check_output(["docker", "network", "inspect", NETWORK]))[0]
    target = validate_target(container, network)
    print(
        json.dumps(
            {
                "network": NETWORK,
                "target": target,
                "listen": "127.0.0.1",
                "ports": PORTS,
                "maxConnections": MAX_CONNECTIONS,
                "idleTimeoutSeconds": IDLE_TIMEOUT,
            }
        ),
        flush=True,
    )
    if not args.check:
        asyncio.run(serve(target))


if __name__ == "__main__":
    main()
