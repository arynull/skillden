import hashlib
import json
import os
import sqlite3
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from .manifest import validate_bundle


class RegistryError(Exception):
    pass


class Registry:
    def __init__(self, data_dir=None):
        if data_dir is None:
            env_dir = os.environ.get("SKILLDEN_DATA_DIR")
            data_dir = Path(env_dir) if env_dir else Path.home() / ".skillden"
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "bundles").mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "registry.db"
        self.init_db()

    def _connect(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS skills(
                    id INTEGER PRIMARY KEY,
                    name TEXT UNIQUE NOT NULL,
                    description TEXT NOT NULL,
                    author TEXT NOT NULL,
                    license TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS versions(
                    id INTEGER PRIMARY KEY,
                    skill_id INTEGER NOT NULL REFERENCES skills(id),
                    version TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    source_url TEXT,
                    manifest_json TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    UNIQUE(skill_id, version)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS installs(
                    id INTEGER PRIMARY KEY,
                    skill_name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    agent TEXT NOT NULL,
                    dest_path TEXT NOT NULL,
                    installed_at TEXT NOT NULL
                )
                """
            )

    @staticmethod
    def _now_iso():
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _hash_bundle_dir(bundle_dir: Path):
        bundle_dir = Path(bundle_dir)
        entries = []
        for p in bundle_dir.rglob("*"):
            if p.is_file():
                rel = p.relative_to(bundle_dir).as_posix()
                h = hashlib.sha256(p.read_bytes()).hexdigest()
                entries.append((rel, h))
        entries.sort(key=lambda x: x[0])
        joined = ";".join(f"{rel}:{hexdigest}" for rel, hexdigest in entries)
        content_sha256 = hashlib.sha256(joined.encode("utf-8")).hexdigest()
        return content_sha256, entries

    def add_skill(self, manifest: dict, bundle_dir) -> tuple[str, str]:
        bundle_path = Path(bundle_dir)
        validate_bundle(manifest, bundle_path)
        name, version = manifest["name"], manifest["version"]
        hashes = []
        for rel in sorted(manifest["files"]):
            h = hashlib.sha256()
            with open(bundle_path / rel, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            hashes.append(f"{rel}:{h.hexdigest()}")
        content_sha256 = hashlib.sha256(";".join(hashes).encode()).hexdigest()
        author = name.split("/")[0]
        now = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT OR IGNORE INTO skills "
                "(name, description, author, license, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (name, manifest["description"], author, manifest["license"], now),
            )
            cur.execute("SELECT id FROM skills WHERE name = ?", (name,))
            skill_id = cur.fetchone()["id"]
            try:
                cur.execute(
                    "INSERT INTO versions "
                    "(skill_id, version, content_sha256, source_url, "
                    "manifest_json, published_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        skill_id,
                        version,
                        content_sha256,
                        manifest.get("source_url"),
                        json.dumps(manifest),
                        now,
                    ),
                )
            except sqlite3.IntegrityError:
                raise RegistryError(
                    f"version already registered: {name}@{version}"
                ) from None
            conn.commit()
        bundles = Path(self.data_dir) / "bundles"
        bundles.mkdir(parents=True, exist_ok=True)
        zip_path = bundles / f"{content_sha256}.zip"
        if not zip_path.exists():
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for rel in manifest["files"]:
                    zf.write(bundle_path / rel, rel)
        return name, version

    def bundle_zip(self, content_sha256) -> Path:
        p = self.data_dir / "bundles" / f"{content_sha256}.zip"
        if not p.is_file():
            raise RegistryError(f"bundle not found: {content_sha256}")
        return p

    def search(self, query) -> list[dict]:
        pattern = f"%{query}%"
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM skills WHERE name LIKE ? "
                "OR description LIKE ? OR author LIKE ?",
                (pattern, pattern, pattern),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_skill(self, name):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM skills WHERE name = ?", (name,)
            ).fetchone()
            if row is None:
                return None
            return dict(row)

    def get_version(self, name, version) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT v.id as id, v.skill_id as skill_id, s.name as name,
                       v.version as version, v.content_sha256 as content_sha256,
                       v.source_url as source_url, v.manifest_json as manifest_json,
                       v.published_at as published_at
                FROM versions v JOIN skills s ON v.skill_id = s.id
                WHERE s.name = ? AND v.version = ?
                """,
                (name, version),
            ).fetchone()
            if row is None:
                return None
            d = dict(row)
            try:
                d["manifest"] = json.loads(d["manifest_json"])
            except (ValueError, TypeError):
                # Corrupt manifest JSON; fall back to empty.
                d["manifest"] = {}
            # Compatibility aliases
            d["skill_name"] = d.get("name")
            return d

    def list_versions(self, name) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT v.version as version
                FROM versions v JOIN skills s ON v.skill_id = s.id
                WHERE s.name = ?
                ORDER BY v.published_at ASC, v.id ASC
                """,
                (name,),
            ).fetchall()
            return [r["version"] for r in rows]

    def record_install(self, name, version, agent, dest_path):
        now = self._now_iso()
        with self._connect() as conn:
            # Replace any existing row for same (skill_name, agent) to avoid duplicates.
            conn.execute(
                "DELETE FROM installs WHERE skill_name = ? AND agent = ?",
                (name, agent),
            )
            conn.execute(
                "INSERT INTO installs(skill_name, version, agent, "
                "dest_path, installed_at) VALUES (?, ?, ?, ?, ?)",
                (name, version, agent, str(dest_path), now),
            )

    def remove_install(self, name, agent):
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM installs WHERE skill_name = ? AND agent = ?",
                (name, agent),
            )

    def list_installs(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM installs").fetchall()
            return [dict(r) for r in rows]
