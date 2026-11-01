"""Optional live encoder compatibility, truncation and Vietnamese query checks."""

import math
from itertools import islice
from typing import cast

from qdrant_client import QdrantClient

from src.core.migration import VerificationReport
from src.ingestion.indexing.index_migration import TeamIndexMigration, write_model
from src.retrieval.embedding import BGEEncoder


def audit_model(
    migration: TeamIndexMigration,
    client: QdrantClient,
    report: VerificationReport,
    device: str = "auto",
) -> VerificationReport:
    """Use eight original texts to check compatibility before encoding queries."""
    encoder = BGEEncoder(migration.spec.model_id, device, migration.spec.max_seq_length)
    model = encoder.model
    report.model_revision = encoder.revision
    samples = list(islice(migration.iter_points(), 8))
    vectors = model.encode(
        [(p.payload or {})["embedding_text"] for p in samples],
        batch_size=1,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    cosines: list[float] = []
    for point, vector in zip(samples, vectors, strict=True):
        original = cast(list[float], point.vector)
        cosine = sum(a * float(b) for a, b in zip(original, vector, strict=True)) / math.sqrt(
            sum(a * a for a in original) * sum(float(b) ** 2 for b in vector)
        )
        cosines.append(cosine)
    report.compatibility_min_cosine = min(cosines)
    write_model(migration.verification_path, report)
    if min(cosines) < 0.999:
        raise ValueError("Encoder compatibility check failed; existing source vectors retained")
    report.truncation_count = 0
    report.max_input_tokens = 0
    # Audit full embedding input, including breadcrumb/title, without truncation.
    import json

    with migration.paths.chunks.open(encoding="utf-8") as stream:
        while lines := list(islice(stream, 128)):
            texts = [json.loads(line)["embedding_text"] for line in lines]
            encoded = model.tokenizer(
                texts,
                truncation=False,
                padding=False,
                return_attention_mask=False,
                return_token_type_ids=False,
                verbose=False,
            )
            sizes = [len(ids) for ids in encoded["input_ids"]]
            report.truncation_count += sum(n > migration.spec.max_seq_length for n in sizes)
            report.max_input_tokens = max(report.max_input_tokens, max(sizes))
    for text in [
        "Thời hiệu khởi kiện tranh chấp hợp đồng là bao lâu?",
        "Điều kiện hưởng lương hưu của người lao động là gì?",
        "Quy định xử phạt vi phạm giao thông đường bộ",
    ]:
        vector = model.encode(text, normalize_embeddings=True, convert_to_numpy=True)
        result = client.query_points(report.collection, query=vector.tolist(), limit=3)
        if not result.points or any(
            not math.isfinite(p.score) or not p.payload for p in result.points
        ):
            raise ValueError("Vietnamese query returned invalid results")
        report.text_queries.append(
            {
                "question": text,
                "results": [
                    {
                        "point_uuid": str(p.id),
                        "score": p.score,
                        "breadcrumb": (p.payload or {}).get("breadcrumb"),
                    }
                    for p in result.points
                ],
            }
        )
    write_model(migration.verification_path, report)
    return report
