"""Authenticated encrypted archives for logical database backups.

Keys are independent 32-byte random values, base64 encoded in the environment.
Only encrypted archives may leave the private temporary directory.
"""
import base64
import os
from pathlib import Path
import sqlite3
import tempfile

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from database_backup import export_backup

MAGIC = b'SENTINELS-BACKUP\x01'
MAX_BYTES = 256 * 1024 * 1024


def archive_key():
    try:
        key = base64.b64decode(os.environ.get('SENTINELS_BACKUP_KEY', '').strip(), validate=True)
    except (ValueError, UnicodeError):
        raise ValueError('SENTINELS_BACKUP_KEY must be base64 for 32 random bytes.') from None
    if len(key) != 32:
        raise ValueError('SENTINELS_BACKUP_KEY must be base64 for 32 random bytes.')
    return key


def _exclusive_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Never replace an earlier recovery point. Private mode on POSIX;
    # Windows also inherits the destination directory's access controls.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _validate(path):
    conn = sqlite3.connect(f'{Path(path).resolve().as_uri()}?mode=ro', uri=True)
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'users', 'sessions', 'scans'} <= tables:
            raise ValueError('Snapshot is not an initialized Sentinels database.')
        if conn.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('Snapshot integrity check failed.')
        if conn.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Snapshot has foreign-key violations.')
    finally:
        conn.close()


def export_encrypted(destination):
    key = archive_key()  # Fail before contacting the database if no valid key.
    if Path(destination).exists():
        raise FileExistsError('Archive already exists.')
    with tempfile.TemporaryDirectory(prefix='sentinels-private-backup-') as directory:
        snapshot = export_backup(Path(directory) / 'snapshot.db')
        if snapshot.stat().st_size > MAX_BYTES:
            raise ValueError('Snapshot exceeds the 256 MiB archive limit.')
        _validate(snapshot)
        plaintext = snapshot.read_bytes()
        nonce = os.urandom(12)
        encrypted = AESGCM(key).encrypt(nonce, plaintext, MAGIC)
        # Authenticate/decrypt before publishing the recovery point.
        if AESGCM(key).decrypt(nonce, encrypted, MAGIC) != plaintext:
            raise ValueError('Archive verification failed.')
        _exclusive_write(destination, MAGIC + nonce + encrypted)
    return Path(destination)


def decrypt_archive(source, destination):
    key = archive_key()
    if Path(source).stat().st_size > MAX_BYTES + len(MAGIC) + 12 + 16:
        raise ValueError('Archive exceeds the supported size.')
    data = Path(source).read_bytes()
    if not data.startswith(MAGIC) or len(data) < len(MAGIC) + 12 + 16:
        raise ValueError('Unknown or truncated backup archive.')
    nonce = data[len(MAGIC):len(MAGIC) + 12]
    # Authentication must succeed before any plaintext is written.
    plaintext = AESGCM(key).decrypt(nonce, data[len(MAGIC) + 12:], MAGIC)
    with tempfile.TemporaryDirectory(prefix='sentinels-private-restore-') as directory:
        snapshot = Path(directory) / 'snapshot.db'
        _exclusive_write(snapshot, plaintext)
        _validate(snapshot)
        _exclusive_write(destination, plaintext)
    return Path(destination)
