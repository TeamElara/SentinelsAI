import base64
import sqlite3

from cryptography.exceptions import InvalidTag
import pytest

import backup_archive
import db
from database_backup import restore_backup
from models import ScanReport
from storage.scans import get_scan, save_scan
from storage.users import sign_in, user_for_token_hash


@pytest.fixture
def backup_key(monkeypatch):
    monkeypatch.setenv('SENTINELS_BACKUP_KEY', base64.b64encode(b'k' * 32).decode())


@pytest.mark.parametrize('driver', ['sqlite', 'libsql'])
def test_encrypted_backup_roundtrip_restores_sessions_scans_and_ledger(driver, monkeypatch, tmp_path, backup_key):
    monkeypatch.delenv('TURSO_DATABASE_URL', raising=False)
    monkeypatch.delenv('RENDER', raising=False)
    monkeypatch.setenv('SENTINELS_DB_DRIVER', driver)
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'source.db')
    db.init_db()
    user = sign_in(123, 'fixture', None, 'private-session-hash', '2099-01-01T00:00:00+00:00')
    save_scan(ScanReport(id='archive-scan', url='https://example.com', scanned_at='now', duration_ms=1,
                        score=65, grade='D'), user_id=user.id)
    archive = backup_archive.export_encrypted(tmp_path / 'backup.sbk')
    assert b'private-session-hash' not in archive.read_bytes()
    assert not archive.read_bytes().startswith(b'SQLite format 3')
    snapshot = backup_archive.decrypt_archive(archive, tmp_path / 'recovered.db')
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'restored.db')
    restore_backup(snapshot)
    db.init_db()
    assert user_for_token_hash('private-session-hash').id == user.id
    assert get_scan('archive-scan').score == 65
    with sqlite3.connect(snapshot) as conn:
        assert conn.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0] == len(db.MIGRATIONS)


def test_wrong_key_and_tampering_never_publish_plaintext(temp_db, tmp_path, monkeypatch, backup_key):
    archive = backup_archive.export_encrypted(tmp_path / 'backup.sbk')
    monkeypatch.setenv('SENTINELS_BACKUP_KEY', base64.b64encode(b'x' * 32).decode())
    output = tmp_path / 'plaintext.db'
    with pytest.raises(InvalidTag):
        backup_archive.decrypt_archive(archive, output)
    assert not output.exists()
    monkeypatch.setenv('SENTINELS_BACKUP_KEY', base64.b64encode(b'k' * 32).decode())
    data = bytearray(archive.read_bytes())
    data[-1] ^= 1
    archive.write_bytes(data)
    with pytest.raises(InvalidTag):
        backup_archive.decrypt_archive(archive, output)
    assert not output.exists()


def test_backup_and_decrypt_refuse_overwriting_recovery_points(temp_db, tmp_path, backup_key):
    archive = backup_archive.export_encrypted(tmp_path / 'backup.sbk')
    original = archive.read_bytes()
    with pytest.raises(FileExistsError):
        backup_archive.export_encrypted(archive)
    assert archive.read_bytes() == original
    output = tmp_path / 'keep.db'
    output.write_bytes(b'keep')
    with pytest.raises(FileExistsError):
        backup_archive.decrypt_archive(archive, output)
    assert output.read_bytes() == b'keep'


@pytest.mark.parametrize('key', ['', 'bad-base64', base64.b64encode(b'too short').decode()])
def test_invalid_key_fails_before_database_access(monkeypatch, tmp_path, key):
    monkeypatch.setenv('SENTINELS_BACKUP_KEY', key)
    def forbidden(*args):
        raise AssertionError('Database must not be accessed without an encryption key')
    monkeypatch.setattr(backup_archive, 'export_backup', forbidden)
    with pytest.raises(ValueError, match='32 random bytes'):
        backup_archive.export_encrypted(tmp_path / 'backup.sbk')
    assert not (tmp_path / 'backup.sbk').exists()


def test_blank_database_is_not_published_as_a_recovery_point(monkeypatch, tmp_path, backup_key):
    def blank(destination):
        sqlite3.connect(destination).close()
        return destination
    monkeypatch.setattr(backup_archive, 'export_backup', blank)
    with pytest.raises(ValueError, match='initialized Sentinels'):
        backup_archive.export_encrypted(tmp_path / 'backup.sbk')
    assert not (tmp_path / 'backup.sbk').exists()


def test_size_limit_never_publishes_a_partial_archive(temp_db, tmp_path, monkeypatch, backup_key):
    monkeypatch.setattr(backup_archive, 'MAX_BYTES', 1)
    with pytest.raises(ValueError, match='256 MiB'):
        backup_archive.export_encrypted(tmp_path / 'backup.sbk')
    assert not (tmp_path / 'backup.sbk').exists()
