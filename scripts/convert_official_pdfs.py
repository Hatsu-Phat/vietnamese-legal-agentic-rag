#!/usr/bin/env python3
"""Convert downloaded official legal PDFs to a legal_corpus.json-compatible file.

Input: a sources manifest (``documents[].document_number``, ``local_dir``, ``files[].file``)
and the folder that holds ``<local_dir>/<file>``. Files of one document are read in manifest order.
Output: ``--output`` (list of ``{law_id, articles}``) and ``<output stem>_report.json``.
Nothing is written to Qdrant or to data/raw.

Example:
  python scripts/convert_official_pdfs.py --manifest data/external/t0101/sources_manifest.json \\
      --output data/external/t0101/legal_corpus_t0101.json
"""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ingestion.official_pdf import convert_pdfs


def main() -> int:
    """Convert every manifest document; exit 1 if any document yields no article."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, help="default: <manifest folder>/raw")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw_dir = args.raw_dir or args.manifest.parent / "raw"
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))

    records, reports = [], []
    for doc in manifest["documents"]:
        paths = [raw_dir / doc["local_dir"] / f["file"] for f in doc["files"]]
        record, report = convert_pdfs(doc["document_number"], paths)
        records.append(record)
        reports.append(asdict(report))
        print(
            f"{record['law_id']:16} articles={report.article_count:4} "
            f"repealed={len(report.repealed_article_ids)} empty={report.empty_article_ids} "
            f"duplicates={report.duplicate_article_ids} scans={report.image_only_pages} "
            f"trimmed={report.trimmed_tail_article_id}"
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
    report_path = args.output.with_name(f"{args.output.stem}_report.json")
    report_path.write_text(json.dumps(reports, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Wrote {len(records)} documents to {args.output} and report to {report_path}")
    return 1 if any(r["article_count"] == 0 for r in reports) else 0


if __name__ == "__main__":
    sys.exit(main())
