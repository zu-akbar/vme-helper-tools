"""Convert local .mb files to .ma — run under mayapy.exe."""

import glob
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
sys.path.insert(0, REPO_ROOT)

from scripts.maya_convert import convert_mb_to_ma, initialize_maya, uninitialize_maya


def main():
    input_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        REPO_ROOT, "data", "input", "VX0019900-transformation-test"
    )
    mb_files = glob.glob(os.path.join(input_dir, "*.mb"))
    if not mb_files:
        print(f"No .mb files found in {input_dir}")
        return

    print(f"Found {len(mb_files)} .mb file(s) in {input_dir}")
    initialize_maya()
    try:
        for mb_path in mb_files:
            ma_path = mb_path.replace(".mb", ".ma")
            print(f"Converting: {os.path.basename(mb_path)} -> {os.path.basename(ma_path)}")
            convert_mb_to_ma(mb_path, ma_path)
            print(f"  Done: {ma_path}")
    finally:
        uninitialize_maya()

    print("All conversions complete.")


if __name__ == "__main__":
    main()
