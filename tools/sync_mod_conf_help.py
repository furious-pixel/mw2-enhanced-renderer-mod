"""Validate or update shipped mod.conf help from the canonical schema."""

import argparse
import configparser
import sys
import textwrap
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
MOD_DIR = ROOT_DIR / "mw2mods"
if str(MOD_DIR) not in sys.path:
    sys.path.insert(0, str(MOD_DIR))

from mod_init import MOD_CONF_PATH, config_schema_records  # noqa: E402


RECORDS_BY_SECTION = {}
for record in config_schema_records():
    if record["file"] == Path(MOD_CONF_PATH).name:
        RECORDS_BY_SECTION.setdefault(record["section"], []).append(record)


def _comment_lines(record):
    help_text = record["help"]
    if "choices" in record:
        help_text += " Choices: " + ", ".join(record["choices"]) + "."
    elif "minimum" in record:
        help_text += f" Range: {record['minimum']} to {record['maximum']}."
    return textwrap.wrap(help_text, width=76)


def _render_profile(parser):
    schema_sections = set(RECORDS_BY_SECTION)
    if set(parser.sections()) != schema_sections:
        raise ValueError("mod.conf sections do not match CONFIG_SCHEMA")

    lines = [
        "# This file is the shipped settings profile. Missing or invalid settings use",
        "# the safe schema fallbacks declared in mod_init.py, which may intentionally",
        "# differ. Setting help and constraints are generated from the same schema data",
        "# available to configuration tools.",
    ]
    for section_name in parser.sections():
        records = RECORDS_BY_SECTION[section_name]
        if set(parser.options(section_name)) != {
            record["name"] for record in records
        }:
            raise ValueError(f"[{section_name}] keys do not match CONFIG_SCHEMA")
        lines.extend(("", f"[{section_name}]"))
        for record_index, record in enumerate(records):
            name = record["name"]
            lines.extend(f"# {line}" for line in _comment_lines(record))
            lines.append(f"{name} = {parser.get(section_name, name)}")
            if record_index + 1 < len(records):
                lines.append("")
    return "\n".join(lines) + "\n"


def main():
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument(
        "--write", action="store_true", help="update the shipped profile"
    )
    arguments = argument_parser.parse_args()

    path = Path(MOD_CONF_PATH)
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path, encoding="utf-8")
    rendered = _render_profile(parser)
    current = path.read_text(encoding="utf-8")
    if rendered == current:
        return
    if not arguments.write:
        raise SystemExit("mod.conf help is out of date; rerun with --write")
    path.write_text(rendered, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
