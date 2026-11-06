"""Strict local XSD + EN16931 Schematron validation in a fresh native process.

No PHP dependency and no HTTP Saxon server. The XSLT and CodeDB come from the
exact pinned factur-x wheel. A native crash, timeout or missing rules is fatal.
"""

import json
import sys
from importlib.resources import files
from pathlib import Path

from facturx import xml_check_xsd
from lxml import etree
from saxonche import PySaxonProcessor


def validate(path):
    raw = path.read_bytes()
    if (
        len(raw) > 10_000_000
        or b"<!DOCTYPE" in raw.upper()
        or b"<!ENTITY" in raw.upper()
    ):
        raise ValueError("XML interdit ou trop volumineux")
    etree.fromstring(raw, etree.XMLParser(resolve_entities=False, no_network=True))
    xml_check_xsd(raw, flavor="factur-x", level="en16931")
    stylesheet = files("facturx").joinpath(
        "xsd_and_schematron/facturx-en16931/FACTUR-X_EN16931.xslt"
    )
    with PySaxonProcessor(license=False) as processor:
        processor.set_configuration_property(
            "http://saxon.sf.net/feature/allowedProtocols", "file"
        )
        compiled = processor.new_xslt30_processor().compile_stylesheet(
            stylesheet_file=str(stylesheet)
        )
        result = compiled.transform_to_string(source_file=str(path.resolve()))
    report = etree.fromstring(
        result.encode(), etree.XMLParser(resolve_entities=False, no_network=True)
    )
    assertions = [
        {
            "id": item.get("id"),
            "severity": item.get("flag", "fatal").lower(),
            "message": " ".join("".join(item.itertext()).split()),
        }
        for item in report.findall(".//{http://purl.oclc.org/dsdl/svrl}failed-assert")
    ]
    # Preserve advisory diagnostics. A CII delivery group is required by its XSD,
    # but may legitimately be empty when no delivery date/address was supplied.
    failures = [
        item for item in assertions if item["severity"] not in {"warning", "info"}
    ]
    warnings = [item for item in assertions if item["severity"] in {"warning", "info"}]
    return {
        "valid": not failures,
        "xsd": True,
        "schematron": not failures,
        "failures": failures,
        "warnings": warnings,
    }


if __name__ == "__main__":
    try:
        result = validate(Path(sys.argv[1]))
        print(json.dumps(result, ensure_ascii=False))
        sys.exit(0 if result["valid"] else 1)
    except Exception as error:
        print(json.dumps({"valid": False, "error": str(error)}, ensure_ascii=False))
        sys.exit(2)
