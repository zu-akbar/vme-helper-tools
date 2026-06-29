"""Output formatting for TC API CLI."""

import csv
import io
import json
import sys


def format_json(data, indent=2):
    return json.dumps(data, indent=indent, ensure_ascii=False, default=str)


def format_csv(data, fields=None):
    if not data:
        return ""
    if not isinstance(data, list):
        data = [data]
    if fields is None:
        fields = list(data[0].keys())
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(data)
    return output.getvalue()


def write_output(content, output_path=None):
    if output_path:
        import os
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
    else:
        sys.stdout.write(content)
        if not content.endswith("\n"):
            sys.stdout.write("\n")
