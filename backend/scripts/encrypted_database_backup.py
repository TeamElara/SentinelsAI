"""Export encrypted Turso snapshots, or decrypt an archive for empty-DB restore.

Reads credentials only from the process environment; never loads .env.
"""
import argparse
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backup_archive import decrypt_archive, export_encrypted


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    operations = parser.add_subparsers(dest='operation', required=True)
    export = operations.add_parser('export')
    export.add_argument('destination', type=Path)
    decrypt = operations.add_parser('decrypt')
    decrypt.add_argument('archive', type=Path)
    decrypt.add_argument('destination', type=Path)
    args = parser.parse_args()
    try:
        if args.operation == 'export':
            if not (os.environ.get('TURSO_DATABASE_URL') and os.environ.get('TURSO_AUTH_TOKEN')):
                raise ValueError('Remote Turso configuration is required.')
            export_encrypted(args.destination)
        else:
            decrypt_archive(args.archive, args.destination)
    except Exception as exc:
        # Database errors and archive contents must not reach public CI logs.
        print(f'Backup {args.operation} failed ({type(exc).__name__}). Verify the key, remote configuration, destination and database integrity.', file=sys.stderr)
        return 1
    print(f'Backup {args.operation} completed and verified.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
