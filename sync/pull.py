"""The PC side of the sync: pull the collector's new rows into the local trading_app.db.

Opens an SSH tunnel to the VM, asks server.py (on the VM's 127.0.0.1) for the schema,
then each table's missing rows, and writes them into the local replica. The replica
is read-only in spirit: the VM is the one writer, so never run the collector or
`bot_registry.py mark` against the PC copy -- do it on the VM (`ssh ... python ...`).

    python sync/pull.py --ssh azureuser@<vm-ip> --key ~/.ssh/<your-key>.pem
    python sync/pull.py --ssh azureuser@<vm-ip> --key ... --watch 3600     # every hour
    python sync/pull.py --target 127.0.0.1:50051                           # tunnel already open

Run from the trading repo root. The local DB defaults to backpack/trading_app.db.
The server side is sync/server.py (on the VM); it shares dbsync.proto, its stubs and
tables.py with this file. Safe to stop at any point: every batch is committed, and the
next run carries on from what the replica has.
"""
import argparse
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time

import grpc

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dbsync_pb2 as pb  # noqa: E402
import dbsync_pb2_grpc as pb_grpc  # noqa: E402
from tables import DEFAULT_PORT, MAX_MESSAGE, TABLES  # noqa: E402

DEFAULT_DB = os.path.join(os.path.dirname(HERE), "backpack", "trading_app.db")


class SyncError(RuntimeError):
    pass


def from_value(v):
    kind = v.WhichOneof("v")
    return None if kind in (None, "null") else getattr(v, kind)


def _if_not_exists(sql):
    return re.sub(r"^\s*CREATE\s+(UNIQUE\s+)?(TABLE|INDEX)\s+(?!IF\s+NOT\s+EXISTS)",
                  lambda m: f"CREATE {m.group(1) or ''}{m.group(2)} IF NOT EXISTS ", sql, flags=re.I)


def _local_cols(con, table):
    return {r[1] for r in con.execute(f'PRAGMA table_info("{table}")')}


def _column_defs(create_sql, table):
    """{column: 'TYPE [DEFAULT x]'} from the VM's CREATE TABLE, so a column the collector
    added since is added here with its type and default -- old rows then read the default
    (the kind columns: 1 / 0, the plain good), not NULL."""
    mem = sqlite3.connect(":memory:")
    try:
        mem.execute(create_sql)
        return {name: (typ or "") + (f" DEFAULT {dflt}" if dflt is not None else "")
                for _, name, typ, _, dflt, _ in mem.execute(f'PRAGMA table_info("{table}")')}
    except sqlite3.Error:
        return {}
    finally:
        mem.close()


def _max_rowid(con, table):
    return con.execute(f'SELECT max(rowid) FROM "{table}"').fetchone()[0] or 0


def _count(con, table):
    return con.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]


def _max_col(con, table, col):
    return con.execute(f'SELECT max("{col}") FROM "{table}"').fetchone()[0]


def pull_once(stub, con, log=print):
    """One sync round. Returns {table: (rows sent, rows new)} -- new = the table's row-count
    growth; the rest were re-sent rows overwritten in place ("refreshed")."""
    schema = stub.Schema(pb.SchemaRequest())
    remote = {t.name: t for t in schema.tables}

    # Tables first (same DDL as the source), then their indexes.
    for t in schema.tables:
        con.execute(_if_not_exists(t.create_sql))
    for sql in schema.index_sql:
        con.execute(_if_not_exists(sql))
    con.commit()

    # The replica can only trail the source. If it is ahead, the VM's DB was not
    # seeded from this one (or was reset) and appending by id would silently skip
    # or collide rows -- stop instead.
    for name, mode, _ in TABLES:
        if mode == "append" and name in remote:
            mine = _max_rowid(con, name)
            if mine > remote[name].max_rowid:
                raise SyncError(
                    f"{name}: the replica is ahead of the VM (local max id {mine}, VM "
                    f"{remote[name].max_rowid}). The VM's DB is not a continuation of this "
                    f"one -- seed it from this file, or pull into a fresh --db.")

    for name in remote:
        if name not in {n for n, _, _ in TABLES}:
            log(f"[pull] {name}: not in sync/tables.py, skipped (add it there)")

    written = {}
    for name, mode, col in TABLES:
        if name not in remote:
            continue
        req = pb.PullRequest(table=name)
        if mode == "append":
            req.after_rowid = _max_rowid(con, name)
        elif mode == "changed":
            since = _max_col(con, name, col)
            if since is not None:
                req.since = str(since)
        n, have = 0, _local_cols(con, name)
        defs = _column_defs(remote[name].create_sql, name)
        before = _count(con, name)
        for batch in stub.Pull(req):
            if not batch.rows:
                continue
            cols = list(batch.columns)
            for c in cols:                  # a column the collector added since
                if c not in have:
                    con.execute(f'ALTER TABLE "{name}" ADD COLUMN "{c}" {defs.get(c, "")}'.rstrip())
                    have.add(c)
            sql = (f'INSERT OR REPLACE INTO "{name}" ({", ".join(chr(34) + c + chr(34) for c in cols)}) '
                   f'VALUES ({", ".join("?" * len(cols))})')
            con.executemany(sql, ([from_value(v) for v in r.cells] for r in batch.rows))
            con.commit()                    # per batch: an interrupted pull keeps its progress
            n += len(batch.rows)
        written[name] = (n, _count(con, name) - before)
    return written


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Tunnel:
    """`ssh -N -L` to the VM's gRPC port, for the life of a `with` block."""

    def __init__(self, dest, key=None, remote_port=DEFAULT_PORT):
        self.dest, self.key, self.remote_port = dest, key, remote_port
        self.port, self.proc = _free_port(), None

    def __enter__(self):
        cmd = ["ssh", "-N", "-L", f"127.0.0.1:{self.port}:127.0.0.1:{self.remote_port}",
               "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=30",
               "-o", "BatchMode=yes"]
        if self.key:
            cmd += ["-i", os.path.expanduser(self.key)]
        self.proc = subprocess.Popen(cmd + [self.dest], stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        deadline = time.time() + 20
        while time.time() < deadline:
            if self.proc.poll() is not None:
                err = self.proc.stderr.read().decode(errors="replace").strip()
                raise SyncError(f"ssh exited ({self.proc.returncode}): {err}")
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=1).close()
                return f"127.0.0.1:{self.port}"
            except OSError:
                time.sleep(0.3)
        self.__exit__()
        raise SyncError("ssh tunnel did not come up in 20 s")

    def __exit__(self, *exc):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def run_round(target, db_path):
    opts = [("grpc.max_receive_message_length", MAX_MESSAGE)]
    with grpc.insecure_channel(target, options=opts) as ch:
        grpc.channel_ready_future(ch).result(timeout=20)
        con = sqlite3.connect(db_path, timeout=60)
        try:
            con.execute("PRAGMA journal_mode=WAL")
            t0 = time.time()
            written = pull_once(pb_grpc.DbSyncStub(ch), con)
        finally:
            con.close()
    print(f"[pull] {time.strftime('%Y-%m-%d %H:%M:%S')}  {time.time() - t0:.1f}s  {summary(written)}", flush=True)


def summary(written):
    """'new: snapshots +2880, passes +1 | refreshed: pricedb_items 40964, ...'

    new = rows the replica did not have. refreshed = rows re-sent and overwritten in
    place: the "full" tables come whole every pull, and a "changed" table re-sends
    every row whose change column moved (each pass touches every pricedb item)."""
    new = [f"{k} +{nw}" for k, (_, nw) in written.items() if nw]
    ref = [f"{k} {sent - nw}" for k, (sent, nw) in written.items() if sent - nw]
    return ("new: " + (", ".join(new) or "nothing")
            + ("  |  refreshed: " + ", ".join(ref) if ref else ""))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DEFAULT_DB, help="the local replica (default: %(default)s)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--ssh", metavar="USER@HOST", help="open an SSH tunnel to the VM")
    g.add_argument("--target", metavar="HOST:PORT", help="talk to this address directly")
    ap.add_argument("--key", help="SSH private key (.pem)")
    ap.add_argument("--remote-port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--watch", type=float, default=0, help="pull again every N seconds")
    a = ap.parse_args(argv)

    while True:
        try:
            if a.ssh:
                with Tunnel(a.ssh, a.key, a.remote_port) as target:
                    run_round(target, a.db)
            else:
                run_round(a.target, a.db)
        except SyncError as e:
            print(f"[pull] {e}", file=sys.stderr, flush=True)
            if "ahead" in str(e) or not a.watch:
                return 1
        except (grpc.RpcError, grpc.FutureTimeoutError) as e:
            print(f"[pull] VM unreachable: {getattr(e, 'details', lambda: e)()}", file=sys.stderr, flush=True)
            if not a.watch:
                return 1
        if not a.watch:
            return 0
        time.sleep(a.watch)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        # Safe at any point: each batch is committed, the next run resumes.
        print("[pull] stopped", flush=True)
