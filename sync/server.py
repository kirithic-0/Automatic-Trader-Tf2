"""The VM side of the sync: serve trading_app.db's new rows over gRPC.

Listens on 127.0.0.1 only -- the PC comes in through an SSH tunnel (trading/sync/pull.py), so
SSH is the only port open to the internet. Opens the DB read-only per request; the
collector's writes (WAL mode) go on undisturbed.

    python sync/server.py                                   # ../collector/trading_app.db, port 50051
    python sync/server.py --db /path/to.db --port 50051

Run by systemd on the VM (deploy/azure/bptf-sync.service).
"""
import argparse
import os
import sqlite3
import sys
import time
from concurrent import futures

import grpc

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dbsync_pb2 as pb  # noqa: E402
import dbsync_pb2_grpc as pb_grpc  # noqa: E402
from tables import BATCH_BYTES, BATCH_ROWS, DEFAULT_PORT, MAX_MESSAGE, MODES  # noqa: E402

DEFAULT_DB = os.path.join(os.path.dirname(HERE), "collector", "trading_app.db")


def to_value(x):
    if x is None:
        return pb.Value(null=True)
    if isinstance(x, int):
        return pb.Value(i=x)
    if isinstance(x, float):
        return pb.Value(f=x)
    if isinstance(x, str):
        return pb.Value(s=x)
    return pb.Value(b=bytes(x))


def _size(x):
    return len(x) if isinstance(x, (str, bytes)) else 8


class DbSync(pb_grpc.DbSyncServicer):
    def __init__(self, db_path):
        self.db_path = db_path

    def _connect(self):
        # mode=ro: this process can never write the collector's DB.
        uri = "file:" + os.path.abspath(self.db_path).replace("\\", "/") + "?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=30)
        con.text_factory = str
        return con

    def Schema(self, request, context):
        con = self._connect()
        try:
            reply = pb.SchemaReply()
            for name, sql in con.execute(
                    "SELECT name, sql FROM sqlite_master WHERE type = 'table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY rowid"):
                try:
                    mx = con.execute(f'SELECT max(rowid) FROM "{name}"').fetchone()[0] or 0
                except sqlite3.OperationalError:     # WITHOUT ROWID table
                    mx = 0
                reply.tables.add(name=name, create_sql=sql, max_rowid=mx)
            for (sql,) in con.execute(
                    "SELECT sql FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL"):
                reply.index_sql.append(sql)
            return reply
        finally:
            con.close()

    def Pull(self, request, context):
        table = request.table
        if table not in MODES:
            # The name goes into SQL below, so only the known tables are served.
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, f"unknown table {table!r}")
        mode, col = MODES[table]
        con = self._connect()
        t0, n = time.time(), 0
        try:
            if mode == "append":
                # Every append table has an INTEGER PRIMARY KEY, i.e. its key IS the
                # rowid, so the replica gets the same ids by inserting the key column.
                cur = con.execute(f'SELECT * FROM "{table}" WHERE rowid > ? ORDER BY rowid',
                                  (request.after_rowid,))
            elif mode == "changed" and request.since:
                cur = con.execute(f'SELECT * FROM "{table}" WHERE "{col}" >= ?', (request.since,))
            else:
                cur = con.execute(f'SELECT * FROM "{table}"')
            cols = [d[0] for d in cur.description]
            batch, size = pb.RowBatch(columns=cols), 0
            for row in cur:
                batch.rows.add(cells=[to_value(x) for x in row])
                size += sum(_size(x) for x in row)
                n += 1
                if len(batch.rows) >= BATCH_ROWS or size >= BATCH_BYTES:
                    yield batch
                    batch, size = pb.RowBatch(columns=cols), 0
            if batch.rows or n == 0:
                yield batch
        finally:
            con.close()
            print(f"[sync] {table:18} {mode:7} {n:>8} rows  {time.time() - t0:5.1f}s", flush=True)


def serve(db_path, host="127.0.0.1", port=DEFAULT_PORT, block=True):
    if not os.path.exists(db_path):
        raise SystemExit(f"no database at {db_path}")
    opts = [("grpc.max_send_message_length", MAX_MESSAGE),
            ("grpc.max_receive_message_length", MAX_MESSAGE)]
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4), options=opts)
    pb_grpc.add_DbSyncServicer_to_server(DbSync(db_path), server)
    bound = server.add_insecure_port(f"{host}:{port}")
    server.start()
    if block:
        print(f"[sync] serving {db_path} on {host}:{bound}", flush=True)
        server.wait_for_termination()
    return server, bound


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--host", default="127.0.0.1",
                    help="keep 127.0.0.1: the PC reaches it through SSH, nothing else should")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    a = ap.parse_args(argv)
    serve(a.db, a.host, a.port)


if __name__ == "__main__":
    main()
