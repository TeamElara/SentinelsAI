"""Export configured DB, or restore a snapshot into an EMPTY configured DB."""
from pathlib import Path
import argparse
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
from database_backup import export_backup, restore_backup

parser = argparse.ArgumentParser()
parser.add_argument("operation", choices=["export", "restore"])
parser.add_argument("path", type=Path)
args = parser.parse_args()
if args.operation == "export":
    export_backup(args.path)
    print("Backup exported successfully.")
else:
    restore_backup(args.path)
    print("Backup restored into the empty target successfully.")
