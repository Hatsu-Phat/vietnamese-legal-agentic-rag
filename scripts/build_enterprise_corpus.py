#!/usr/bin/env python3
"""Build the P1 processed corpus: existing corpus + official laws, with verified metadata.

Inputs: the processed corpus (``documents.jsonl``, ``provisions.jsonl``), the converted official
corpus (``scripts/convert_official_pdfs.py`` output) and its sources manifest.
Outputs in ``--output-dir`` (must differ from ``--base-dir``): ``documents.jsonl``,
``provisions.jsonl``, ``source_manifest.csv``, ``build_report.json``.
All documents are tagged ``source_kind=law``, ``access_level=public``.
Nothing is written to Qdrant or to the base corpus.

Example:
  python scripts/build_enterprise_corpus.py \\
      --official-corpus data/external/t0101/legal_corpus_t0101.json \\
      --manifest data/external/t0101/sources_manifest.json --output-dir data/processed_p1
"""

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.common.config import resolve_project_path
from src.core.enums import AccessLevel, SourceKind
from src.core.models import LegalDocument, LegalProvision
from src.ingestion.official_metadata import (
    OFFICIAL_SOURCE_DATASET,
    apply_metadata,
    load_metadata_updates,
    merge_corpora,
)
from src.ingestion.source_registry import SourceRegistry
from src.ingestion.thiemcun_adapter import ThiemCunCorpusAdapter


def _read_jsonl[T: (LegalDocument, LegalProvision)](path: Path, model: type[T]) -> list[T]:
    with path.open(encoding="utf-8") as f:
        return [model.model_validate_json(line) for line in f if line.strip()]


def main() -> int:
    """Merge, apply metadata, export; exit 1 if a manifest document is missing from the result."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--base-dir", type=Path, default=Path("data/processed"))
    parser.add_argument("--official-corpus", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    base_dir = resolve_project_path(args.base_dir)
    output_dir = resolve_project_path(args.output_dir)
    if output_dir.resolve() == base_dir.resolve():
        parser.error("--output-dir must differ from --base-dir")

    base_docs = _read_jsonl(base_dir / "documents.jsonl", LegalDocument)
    base_provs = _read_jsonl(base_dir / "provisions.jsonl", LegalProvision)
    adapter = ThiemCunCorpusAdapter(source_dataset_name=OFFICIAL_SOURCE_DATASET)
    official = adapter.process_corpus(json.loads(args.official_corpus.read_text(encoding="utf-8")))
    if official.anomalies:
        print(f"Official corpus anomalies: {len(official.anomalies)}")

    docs, provs, report = merge_corpora(
        base_docs, base_provs, official.documents, official.provisions
    )
    updates = load_metadata_updates(json.loads(args.manifest.read_text(encoding="utf-8")))
    docs, provs, missing = apply_metadata(docs, provs, updates)
    # Every document here is published law: public by definition (internal documents come later).
    law_access = {"source_kind": SourceKind.LAW, "access_level": AccessLevel.PUBLIC}
    docs = [d.model_copy(update=law_access) for d in docs]
    report.updated_documents = sorted(
        u.document_id for u in updates if u.document_id not in missing
    )
    report.missing_documents = missing

    replaced = set(report.replaced_documents)
    new_ids = {p.provision_id for p in provs}
    lost = sorted(
        p.provision_id
        for p in base_provs
        if p.document_id in replaced and p.provision_id not in new_ids
    )

    _, provs_path = adapter.export_processed_jsonl(docs, provs, output_dir=output_dir)
    per_doc = Counter(p.document_id for p in provs)
    chars = Counter[str]()
    for p in provs:
        chars[p.document_id] += p.char_length
    registry = SourceRegistry(manifest_path=output_dir / "source_manifest.csv")
    registry.save_manifest(
        [registry.create_entry(d, per_doc[d.document_id], chars[d.document_id]) for d in docs]
    )
    summary = asdict(report) | {
        "lost_provision_ids_from_replaced_documents": lost,
        "status_counts": dict(Counter(d.status.value for d in docs)),
        "official_anomalies": len(official.anomalies),
    }
    (output_dir / "build_report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(
        f"documents={report.total_documents} provisions={report.total_provisions} "
        f"added={len(report.added_documents)} replaced={report.replaced_documents} "
        f"updated={len(report.updated_documents)} missing={missing} lost={len(lost)} "
        f"status={summary['status_counts']}"
    )
    print(f"Wrote {output_dir} ({provs_path.name}, source_manifest.csv, build_report.json)")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
