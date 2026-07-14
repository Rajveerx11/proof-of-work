"""Hash-chained, signed SQLite log. Tamper-EVIDENT: any edit breaks the chain or a signature.

# ponytail: local Ed25519 key + local sqlite file is tamper-EVIDENT, not tamper-PROOF —
# whoever holds log.key can rewrite the whole chain. v2 upgrade = anchor the signed head
# externally / keyless cosign -> Rekor transparency log. Linear hash-chain + signed head is
# the shipped tier; do NOT build a Merkle Mountain Range for v1.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from hashlib import sha256

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .envelope import canonical

_ZERO = "0" * 64


def _connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS entries("
        "id INTEGER PRIMARY KEY, ts TEXT, prev_hash TEXT, entry_hash TEXT, "
        "envelope_json TEXT, head_sig TEXT)"
    )
    return conn


def _key_path(db_path: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), "log.key")


def _load_or_create_key(db_path: str) -> Ed25519PrivateKey:
    path = _key_path(db_path)
    if os.path.exists(path):
        with open(path, "rb") as f:
            return Ed25519PrivateKey.from_private_bytes(f.read())
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes_raw()
    # atomic-ish write; 0600 best effort (no-op semantics on Windows)
    with open(path, "wb") as f:
        f.write(raw)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


def record(envelope: dict, db_path: str) -> str:
    conn = _connect(db_path)
    try:
        key = _load_or_create_key(db_path)
        # busy_timeout + IMMEDIATE guards the read-then-write against a concurrent writer.
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT entry_hash FROM entries ORDER BY id DESC LIMIT 1"
        ).fetchone()
        prev_hash = row[0] if row else _ZERO
        env_bytes = canonical(envelope)
        entry_hash = sha256(prev_hash.encode() + env_bytes).hexdigest()
        head_sig = key.sign(entry_hash.encode()).hex()
        conn.execute(
            "INSERT INTO entries(ts, prev_hash, entry_hash, envelope_json, head_sig) "
            "VALUES (?,?,?,?,?)",
            (
                datetime.now(timezone.utc).isoformat(),
                prev_hash,
                entry_hash,
                env_bytes.decode(),
                head_sig,
            ),
        )
        conn.commit()
        return entry_hash
    finally:
        conn.close()


def verify_chain(db_path: str) -> bool:
    key_path = _key_path(db_path)
    if not os.path.exists(key_path):
        return False
    with open(key_path, "rb") as f:
        pub: Ed25519PublicKey = Ed25519PrivateKey.from_private_bytes(f.read()).public_key()

    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT prev_hash, entry_hash, envelope_json, head_sig FROM entries ORDER BY id"
        ).fetchall()
    finally:
        conn.close()

    prev = _ZERO
    for prev_hash, entry_hash, envelope_json, head_sig in rows:
        if prev_hash != prev:
            return False
        expected = sha256(prev.encode() + canonical(json.loads(envelope_json))).hexdigest()
        if expected != entry_hash:
            return False
        try:
            pub.verify(bytes.fromhex(head_sig), entry_hash.encode())
        except (InvalidSignature, ValueError):
            return False
        prev = entry_hash
    return True
