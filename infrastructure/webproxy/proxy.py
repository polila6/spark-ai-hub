"""HTTP/CONNECT proxy that only reaches public internet addresses (used by the isolated page browser).

Rules: ports 80/443/8080/8443; the host is resolved here, EVERY address must be global (no private, loopback, link-local,
CGNAT/tailnet, multicast, 6to4/Teredo/NAT64 wrappers); the upstream connection goes to the checked IP, not to the name
(no DNS rebinding); idle, lifetime and byte limits per tunnel."""
import asyncio
import ipaddress
import socket
import time
from urllib.parse import urlsplit

PORT = 3128
PORTS = {80, 443, 8080, 8443}
IDLE = 45
LIFE = 150
MAX_BYTES = 120 * 1024 * 1024
MAX_HEAD = 64 * 1024
MAX_CONN = 256
WRAPPERS = [ipaddress.ip_network(n) for n in ("64:ff9b::/96", "64:ff9b:1::/48", "2002::/16", "2001::/32", "100.64.0.0/10",
                                              "192.0.0.0/24", "198.18.0.0/15")]
sem = asyncio.Semaphore(MAX_CONN)


class Refused(Exception):
    pass


def public(ip: str) -> bool:
    a = ipaddress.ip_address(ip.split("%")[0])
    if a.version == 6 and a.ipv4_mapped:
        a = a.ipv4_mapped
    return a.is_global and not a.is_multicast and not any(a in n for n in WRAPPERS if n.version == a.version)


async def resolve(host: str, port: int) -> list[str]:
    if port not in PORTS:
        raise Refused(f"puerto {port} no permitido")
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise Refused(f"no se puede resolver {host}")
    ips = list(dict.fromkeys(i[4][0] for i in infos))
    if not ips or not all(public(i) for i in ips):
        raise Refused(f"{host} no es una dirección pública")
    return ips


async def connect(host: str, port: int):
    last = None
    for ip in await resolve(host, port):
        try:
            return await asyncio.wait_for(asyncio.open_connection(ip, port), 12)
        except (OSError, asyncio.TimeoutError) as e:
            last = e
    raise Refused(f"no se pudo conectar con {host}: {last}")


async def pipe(r, w, budget):
    try:
        while True:
            data = await asyncio.wait_for(r.read(65536), IDLE)
            if not data:
                break
            budget[0] -= len(data)
            if budget[0] < 0:
                break
            w.write(data)
            await w.drain()
    except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError):
        pass
    finally:
        try:
            w.close()
        except OSError:
            pass


async def reply(w, code: int, msg: str):
    body = msg.encode()
    w.write(f"HTTP/1.1 {code} {msg[:40]}\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
    try:
        await w.drain()
    except OSError:
        pass
    w.close()


async def handle(cr, cw):
    async with sem:
        try:
            head = await asyncio.wait_for(cr.readuntil(b"\r\n\r\n"), 15)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, OSError):
            cw.close()
            return
        try:
            line, _, rest = head.partition(b"\r\n")
            method, target, ver = line.decode("latin-1").split(" ", 2)
            if method.upper() == "CONNECT":
                host, _, p = target.rpartition(":")
                ur, uw = await connect(host.strip("[]"), int(p or 443))
                cw.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
                await cw.drain()
                first = b""
            else:
                u = urlsplit(target)
                if u.scheme != "http" or not u.hostname:
                    raise Refused("solo http(s)")
                ur, uw = await connect(u.hostname, u.port or 80)
                path = (u.path or "/") + (f"?{u.query}" if u.query else "")
                keep = [h for h in rest.split(b"\r\n") if h and not h.lower().startswith((b"proxy-", b"connection:"))]
                first = f"{method} {path} {ver}\r\n".encode() + b"\r\n".join(keep) + b"\r\nConnection: close\r\n\r\n"
        except Refused as e:
            await reply(cw, 403, f"Bloqueado: {e}")
            return
        except (ValueError, OSError):
            await reply(cw, 400, "Petición no válida")
            return
        if first:
            uw.write(first)
        budget = [MAX_BYTES]
        t = asyncio.gather(pipe(cr, uw, budget), pipe(ur, cw, budget))
        try:
            await asyncio.wait_for(t, LIFE)
        except asyncio.TimeoutError:
            pass
        for w in (uw, cw):
            try:
                w.close()
            except OSError:
                pass


async def main():
    srv = await asyncio.start_server(handle, "0.0.0.0", PORT, limit=MAX_HEAD)
    print(f"webproxy listening on :{PORT}", flush=True)
    async with srv:
        await srv.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
