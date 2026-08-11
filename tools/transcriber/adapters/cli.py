#!/usr/bin/env python3
"""CLI-адаптер: python -m adapters.cli запись.m4a [--model medium] [--json]"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from transcriber import TranscriberAgent

ap = argparse.ArgumentParser()
ap.add_argument("file")
ap.add_argument("--model", default="large-v3")
ap.add_argument("--json", action="store_true")
a = ap.parse_args()

tr = TranscriberAgent(model=a.model).transcribe(a.file)
print(tr.to_json() if a.json else tr.to_markdown())
