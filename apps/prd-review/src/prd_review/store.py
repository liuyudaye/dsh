import json
import sqlite3
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone


SCHEMA = """
CREATE TABLE IF NOT EXISTS versions (
    id INTEGER PRIMARY KEY,
    document_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    title TEXT NOT NULL,
    snapshot TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(document_id, revision)
);
CREATE TABLE IF NOT EXISTS review_drafts (
    draft_id TEXT PRIMARY KEY,
    snapshot TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS canvas_versions (
    report_version_id INTEGER NOT NULL REFERENCES versions(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL CHECK(revision >= 0),
    graph TEXT NOT NULL,
    created_at TEXT NOT NULL,
    restored_from INTEGER,
    PRIMARY KEY (report_version_id, revision)
);
"""


class RevisionChanged(Exception):
    pass


class Store:
    def __init__(self, path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self, connection=None):
        if connection is not None:
            yield connection
            return
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            yield db

    def all_documents(self):
        with self.connection() as db:
            return db.execute("""
                SELECT id, title, revision, created_at
                FROM versions AS v
                WHERE v.revision = (SELECT MAX(revision) FROM versions WHERE document_id=v.document_id)
                ORDER BY id DESC
            """).fetchall()

    def get(self, version_id, connection=None):
        with (nullcontext(connection) if connection is not None else self.connection()) as db:
            row = db.execute("SELECT * FROM versions WHERE id=?", (version_id,)).fetchone()
        return dict(row) | {"snapshot": json.loads(row["snapshot"])} if row else None

    def get_draft(self, document):
        with self.connection() as db:
            row = db.execute("SELECT snapshot FROM review_drafts WHERE draft_id=?", (document,)).fetchone()
        return json.loads(row["snapshot"]) if row else None

    def save_draft(self, document, draft, expected, connection=None):
        with self.transaction(connection) as db:
            row = db.execute("SELECT snapshot FROM review_drafts WHERE draft_id=?", (document,)).fetchone()
            if (json.loads(row["snapshot"]) if row else None) != expected:
                raise RevisionChanged
            db.execute("INSERT OR REPLACE INTO review_drafts(draft_id, snapshot) VALUES (?,?)",
                       (document, json.dumps(draft, ensure_ascii=False)))
            db.execute("""DELETE FROM review_drafts WHERE rowid IN (
                       SELECT rowid FROM review_drafts ORDER BY rowid DESC LIMIT -1 OFFSET 32
                       )""")

    def history(self, document_id):
        with self.connection() as db:
            return db.execute("""SELECT id, revision FROM versions
                                 WHERE document_id=? ORDER BY revision DESC""", (document_id,)).fetchall()

    def current(self, document_id, revision):
        with self.connection() as db:
            row = db.execute("SELECT id FROM versions WHERE document_id=? AND revision=?",
                             (document_id, revision)).fetchone()
        return row["id"] if row else None

    def save(self, snapshot, expected_previous, connection=None):
        document_id, revision = snapshot["doc_id"], int(snapshot["revision"])
        with self.transaction(connection) as db:
            current = db.execute("SELECT id, snapshot FROM versions WHERE document_id=? AND revision=?",
                                 (document_id, revision)).fetchone()
            if current:
                if json.loads(current["snapshot"])["source"] != snapshot["source"]:
                    raise RevisionChanged
                return current["id"]
            latest = db.execute("SELECT MAX(revision) FROM versions WHERE document_id=?", (document_id,)).fetchone()[0]
            if latest != expected_previous or (latest is not None and revision <= latest):
                raise RevisionChanged
            return db.execute("""
                INSERT INTO versions(document_id,revision,title,snapshot,created_at) VALUES (?,?,?,?,?)
            """, (document_id, revision, snapshot["review"]["title"], json.dumps(snapshot, ensure_ascii=False),
                  datetime.now(timezone.utc).isoformat())).lastrowid

    def reformat(self, version_id, report, engine, skill, expected_review, connection=None):
        with self.transaction(connection) as db:
            row = db.execute("SELECT snapshot FROM versions WHERE id=?", (version_id,)).fetchone()
            snapshot = json.loads(row["snapshot"])
            if snapshot["review"] != expected_review:
                raise RevisionChanged
            snapshot.setdefault("legacy_review", snapshot["review"])
            snapshot["previous_review"] = snapshot["review"]
            snapshot["review"] = report
            snapshot["engine"] = engine
            snapshot["skill"] = skill
            db.execute("UPDATE versions SET title=?, snapshot=? WHERE id=?",
                       (report["title"], json.dumps(snapshot, ensure_ascii=False), version_id))

    def canvas_history(self, version_id, connection=None):
        with (nullcontext(connection) if connection is not None else self.connection()) as db:
            return db.execute("""
                SELECT revision, created_at, restored_from FROM canvas_versions
                WHERE report_version_id=? ORDER BY revision DESC
            """, (version_id,)).fetchall()

    def canvas_graph(self, version_id, revision, connection=None):
        with (nullcontext(connection) if connection is not None else self.connection()) as db:
            row = db.execute("""
                SELECT graph FROM canvas_versions WHERE report_version_id=? AND revision=?
            """, (version_id, revision)).fetchone()
        return json.loads(row["graph"]) if row else None

    def append_canvas(self, version_id, revision, graph, created_at, connection, restored_from=None):
        """Insert an immutable canvas snapshot within the caller's write transaction."""
        connection.execute("""
            INSERT INTO canvas_versions(report_version_id, revision, graph, created_at, restored_from)
            VALUES (?, ?, ?, ?, ?)
        """, (version_id, revision, json.dumps(graph, ensure_ascii=False, separators=(",", ":")),
              created_at, restored_from))
