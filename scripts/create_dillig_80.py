#!/usr/bin/env python3
"""
Create a balanced 80-file subset from benchmarks/dillig.

The subset keeps only files with prefixes 10-, 15-, 20-, 25-, and 30-.
It selects the first 16 files from each prefix in numeric filename order,
so the result is deterministic, stratified, and easy to inspect.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


GROUPS = ("10", "15", "20", "25", "30")
TARGET_PER_GROUP = 16


def numeric_suffix(path: Path) -> int:
    stem = path.stem
    try:
        return int(stem.split("-", 1)[1])
    except (IndexError, ValueError) as exc:
        raise ValueError(f"Unexpected dillig filename: {path.name}") from exc


def build_subset(src_dir: Path, dst_dir: Path) -> None:
    if not src_dir.exists():
        raise FileNotFoundError(f"Source directory not found: {src_dir}")

    selected: list[Path] = []
    for group in GROUPS:
        group_files = sorted(
            (p for p in src_dir.iterdir() if p.is_file() and p.name.startswith(f"{group}-")),
            key=numeric_suffix,
        )

        if len(group_files) < TARGET_PER_GROUP:
            raise ValueError(
                f"Group {group}- has only {len(group_files)} files, "
                f"need at least {TARGET_PER_GROUP}"
            )

        chosen = group_files[:TARGET_PER_GROUP]
        selected.extend(chosen)

        print(f"{group}-: selected {len(chosen)} of {len(group_files)}")
        for file in chosen:
            print(f"  {file.name}")

    if dst_dir.exists():
        shutil.rmtree(dst_dir)
    dst_dir.mkdir(parents=True, exist_ok=True)

    for file in selected:
        shutil.copy2(file, dst_dir / file.name)

    print(f"\nCreated {dst_dir} with {len(selected)} files.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--src",
        type=Path,
        default=Path("benchmarks/dillig"),
        help="Source benchmark directory",
    )
    parser.add_argument(
        "--dst",
        type=Path,
        default=Path("benchmarks/dillig-80"),
        help="Destination subset directory",
    )
    args = parser.parse_args()

    build_subset(args.src, args.dst)


if __name__ == "__main__":
    main()
