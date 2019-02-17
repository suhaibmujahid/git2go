#!/usr/bin/python3
"""Tool to assist in debugging git2go memory leaks.

In order to use, run this program as root and start the git2go binary with the
`GIT2GO_DEBUG_ALLOCATOR=1` environment variable set. For best results, make
sure that the program exits and calls `git.Shutdown()` at the end to remove
most noise.
"""

import argparse
import dataclasses
import os
import socket
import struct
from typing import Dict, Iterable, Tuple, Sequence


@dataclasses.dataclass
class Allocation:
    """A single object allocation."""

    filename: str
    line: int
    size: int
    ptr: int
    backtrace: Sequence[str]


def _receive_allocation_messages(
        conn: socket.socket) -> Iterable[Tuple[str, Allocation]]:
    while True:
        msg = conn.recv(4096)
        if not msg:
            break
        (message_type, line, ptr, size, file_length,
         _) = struct.unpack('@IiLLLL', msg[:40])
        filename = msg[40:40 + file_length].decode('utf-8')
        message_type = chr(message_type)
        if message_type in ('A', 'R'):
            yield message_type, Allocation(
                filename, line, size, ptr,
                tuple(
                    frame.decode('utf-8') for frame in
                    msg[40 + file_length:].rstrip(b'\x00').split(b'\x00')))
        else:
            yield message_type, Allocation(filename, line, size, ptr, ())


@dataclasses.dataclass
class LeakSummaryEntry:
    """An entry in the leak summary."""

    allocation_count: int
    allocation_size: int
    filename: str
    line: int
    backtrace: Sequence[str]


def _process_leaked_allocations(
        live_allocations: Dict[int, Allocation]) -> None:
    """Print a summary of leaked allocations."""

    if not live_allocations:
        print('No leaks!')
        return

    backtraces: Dict[Sequence[str], LeakSummaryEntry] = {}
    for obj in live_allocations.values():
        if obj.backtrace not in backtraces:
            backtraces[obj.backtrace] = LeakSummaryEntry(
                0, 0, obj.filename, obj.line, obj.backtrace)
        backtraces[obj.backtrace].allocation_count += 1
        backtraces[obj.backtrace].allocation_size += obj.size
    print(f'{"Total size":>20} | {"Average size":>20} | '
          f'{"Allocations":>11} | Filename')
    print(f'{"":=<20}=+={"":=<20}=+={"":=<11}=+={"":=<64}')
    for entry in sorted(backtraces.values(),
                        key=lambda e: e.allocation_size,
                        reverse=True):
        print(f'{entry.allocation_size:20} | '
              f'{entry.allocation_size//entry.allocation_count:20} | '
              f'{entry.allocation_count:11} | '
              f'{entry.filename}:{entry.line}')
        for frame in entry.backtrace:
            print(f'{"":20} | {"":20} | {"":11} | {frame}')
        print(f'{"":-<20}-+-{"":-<20}-+-{"":-<11}-+-{"":-<64}')
    print()


def _handle_connection(conn: socket.socket) -> None:
    """Handle a single connection."""

    live_allocations: Dict[int, Allocation] = {}
    with conn:
        for message_type, allocation in _receive_allocation_messages(conn):
            if message_type in ('A', 'R'):
                live_allocations[allocation.ptr] = allocation
            elif message_type == 'D':
                del live_allocations[allocation.ptr]
            else:
                raise Exception(f'Unknown message type "{message_type}"')
    _process_leaked_allocations(live_allocations)


def main() -> None:
    """Tool to assist in debugging git2go memory leaks."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('socket_path',
                        metavar='SOCKET-PATH',
                        default='/run/git2go_alloc.sock',
                        nargs='?',
                        type=str)
    args = parser.parse_args()

    try:
        os.unlink(args.socket_path)
    except FileNotFoundError:
        pass

    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as sock:
        sock.bind(args.socket_path)
        os.chmod(args.socket_path, 0o666)
        sock.listen(1)
        while True:
            conn, _ = sock.accept()
            _handle_connection(conn)


if __name__ == '__main__':
    main()
