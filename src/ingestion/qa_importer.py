"""Importer for converting legal Q&A benchmarks to standardized QAEvaluationItem schemas.

Imports validation and test splits from raw datasets (best_test_qna.csv, train_qna.csv,
true_valid_results.json, true_test_results.json), maps gold references to deterministic
provision IDs, performs corpus coverage audits, and outputs qa_dev.jsonl and qa_test.jsonl.
"""

import ast
import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.common.exceptions import EvaluationImportError
from src.common.logger import logger
from src.common.text_utils import (
    build_provision_id,
    normalize_document_id,
    normalize_legal_text,
)
from src.core.enums import AnswerableStatus, QuestionType
from src.core.models import QAEvaluationItem


@dataclass
class QACoverageReport:
    """Báo cáo tỷ lệ bao phủ của tập câu hỏi đánh giá đối với corpus điều luật."""

    total_questions: int = 0
    total_gold_provisions: int = 0
    matched_provisions: int = 0
    missing_provisions: int = 0
    questions_fully_covered: int = 0
    questions_partially_covered: int = 0
    questions_zero_covered: int = 0
    coverage_rate_provisions: float = 0.0
    coverage_rate_questions: float = 0.0
    missing_provision_ids: list[str] = field(default_factory=list)


class QAImporter:
    """Xử lý nhập và đối chiếu bộ dữ liệu câu hỏi Q&A pháp luật Việt Nam."""

    def __init__(self, raw_dir: str | Path = "data/raw") -> None:
        self.raw_dir = Path(raw_dir)

    def _find_file(self, *relative_paths: str) -> Path | None:
        """Tìm tệp dữ liệu theo danh sách các đường dẫn ưu tiên."""
        for rel in relative_paths:
            candidate = self.raw_dir / rel
            if candidate.is_file():
                return candidate
        return None

    def load_qa_dict_from_csv(self, csv_path: Path) -> dict[str, dict[str, Any]]:
        """Đọc tệp CSV chứa câu hỏi Q&A và trả về dict ánh xạ question_id -> row data."""
        data: dict[str, dict[str, Any]] = {}
        try:
            with open(csv_path, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    qid = row.get("question_id", "").strip()
                    if qid:
                        data[qid] = row
            logger.info(f"Đã đọc {len(data)} câu hỏi từ {csv_path}")
            return data
        except Exception as e:
            raise EvaluationImportError(
                f"Lỗi khi đọc tệp Q&A CSV: {csv_path}",
                details={"error": str(e)},
            ) from e

    def load_true_results_json(self, json_path: Path) -> dict[str, list[str]]:
        """Đọc tệp JSON chứa gold articles ánh xạ question_id -> list of '{law_id}%{article_id}'."""
        try:
            with open(json_path, encoding="utf-8") as f:
                content: Any = json.load(f)
                if not isinstance(content, dict):
                    raise EvaluationImportError(
                        f"Nội dung {json_path} không phải JSON object (dict)",
                        details={"type": type(content).__name__},
                    )
                return content
        except Exception as e:
            raise EvaluationImportError(
                f"Lỗi khi đọc tệp true results JSON: {json_path}",
                details={"error": str(e)},
            ) from e

    def _parse_gold_references(self, raw_refs: list[str] | str) -> tuple[list[str], list[str]]:
        """Chuyển đổi các chuỗi tham chiếu chuẩn dạng 'law_id%article_id' hoặc literal list of dict.

        Returns:
            Tuple gồm (gold_document_ids, gold_provision_ids).
        """
        doc_ids: list[str] = []
        prov_ids: list[str] = []

        if isinstance(raw_refs, list):
            for ref in raw_refs:
                ref_str = str(ref).strip()
                if "%" in ref_str:
                    law_id, art_id = ref_str.split("%", 1)
                    doc_id = normalize_document_id(law_id)
                    prov_id = build_provision_id(doc_id, art_id)
                    if doc_id not in doc_ids:
                        doc_ids.append(doc_id)
                    if prov_id not in prov_ids:
                        prov_ids.append(prov_id)
        elif isinstance(raw_refs, str) and raw_refs.strip().startswith("["):
            try:
                parsed = ast.literal_eval(raw_refs)
                if isinstance(parsed, list):
                    for item in parsed:
                        if isinstance(item, dict):
                            law_id = str(item.get("law_id", "")).strip()
                            art_id = str(item.get("article_id", "")).strip()
                            if law_id and art_id:
                                doc_id = normalize_document_id(law_id)
                                prov_id = build_provision_id(doc_id, art_id)
                                if doc_id not in doc_ids:
                                    doc_ids.append(doc_id)
                                if prov_id not in prov_ids:
                                    prov_ids.append(prov_id)
            except Exception:
                pass

        return doc_ids, prov_ids

    def import_datasets(
        self,
    ) -> tuple[list[QAEvaluationItem], list[QAEvaluationItem]]:
        """Nạp và chuẩn hóa cả 2 tập đánh giá qa_dev và qa_test.

        Returns:
            Tuple chứa (qa_dev_items, qa_test_items).
        """
        # 1. Tìm các tệp dữ liệu thô
        valid_json = self._find_file(
            "true_results/true_valid_results.json",
            "true_valid_results.json",
        )
        test_json = self._find_file(
            "true_results/true_test_results.json",
            "true_test_results.json",
        )
        train_csv = self._find_file(
            "qna/train_qna.csv",
            "train_qna.csv",
        )
        best_test_csv = self._find_file(
            "qna/best_test_qna.csv",
            "best_test_qna.csv",
            "qna/test_qna.csv",
            "test_qna.csv",
        )

        qa_dev_items: list[QAEvaluationItem] = []
        qa_test_items: list[QAEvaluationItem] = []

        # 2. Xử lý tập Dev (từ true_valid_results.json và train_qna.csv)
        if valid_json and train_csv:
            logger.info("Đang xử lý tập qa_dev từ true_valid_results.json và train_qna.csv...")
            valid_results = self.load_true_results_json(valid_json)
            train_rows = self.load_qa_dict_from_csv(train_csv)

            for qid, refs in valid_results.items():
                row = train_rows.get(qid)
                if not row:
                    continue
                q_text = normalize_legal_text(row.get("question", ""))
                doc_ids, prov_ids = self._parse_gold_references(refs)

                item = QAEvaluationItem(
                    question_id=qid,
                    question_text=q_text,
                    question_type=QuestionType.DIRECT,
                    as_of_date=None,
                    expected_answer=None,
                    gold_document_ids=doc_ids,
                    gold_provision_ids=prov_ids,
                    gold_evidence_spans=[],
                    answerable_status=AnswerableStatus.ANSWERABLE,
                    annotation_source="thiemcun169_valid_split",
                    review_status="unverified_imported",
                )
                qa_dev_items.append(item)

        # 3. Xử lý tập Test (từ best_test_qna.csv và true_test_results.json)
        if best_test_csv:
            logger.info(f"Đang xử lý tập qa_test từ {best_test_csv}...")
            test_rows = self.load_qa_dict_from_csv(best_test_csv)
            true_test_refs = self.load_true_results_json(test_json) if test_json else {}

            dev_qids = {item.question_id for item in qa_dev_items}

            for qid, row in test_rows.items():
                if qid in dev_qids:
                    # Tuyệt đối không cho phép trùng lặp câu hỏi giữa dev và test
                    continue

                q_text = normalize_legal_text(row.get("question", ""))
                # Ưu tiên lấy từ true_test_results.json nếu có, fallback lấy từ relevant_articles CSV
                refs = true_test_refs.get(qid) or row.get("relevant_articles", "")
                doc_ids, prov_ids = self._parse_gold_references(refs)

                item = QAEvaluationItem(
                    question_id=qid,
                    question_text=q_text,
                    question_type=QuestionType.DIRECT,
                    as_of_date=None,
                    expected_answer=None,
                    gold_document_ids=doc_ids,
                    gold_provision_ids=prov_ids,
                    gold_evidence_spans=[],
                    answerable_status=AnswerableStatus.ANSWERABLE,
                    annotation_source="thiemcun169_test_benchmark",
                    review_status="unverified_imported",
                )
                qa_test_items.append(item)

        logger.info(
            f"Đã nhập thành công: {len(qa_dev_items)} câu hỏi dev, "
            f"{len(qa_test_items)} câu hỏi test (hoàn toàn tách biệt không trùng lặp)"
        )
        return qa_dev_items, qa_test_items

    def audit_coverage(
        self,
        qa_items: list[QAEvaluationItem],
        known_provision_ids: set[str],
    ) -> QACoverageReport:
        """Kiểm tra tỷ lệ các gold provisions của tập câu hỏi thực sự nằm trong corpus.

        Args:
            qa_items: Danh sách QAEvaluationItem.
            known_provision_ids: Tập các provision_id hợp lệ đã nạp vào provisions.jsonl.

        Returns:
            QACoverageReport với thống kê chi tiết.
        """
        report = QACoverageReport(total_questions=len(qa_items))
        missing_ids: list[str] = []

        for item in qa_items:
            gold_provs = item.gold_provision_ids
            report.total_gold_provisions += len(gold_provs)
            matched_count = 0

            for pid in gold_provs:
                if pid in known_provision_ids:
                    matched_count += 1
                    report.matched_provisions += 1
                else:
                    report.missing_provisions += 1
                    missing_ids.append(pid)

            if len(gold_provs) > 0 and matched_count == len(gold_provs):
                report.questions_fully_covered += 1
            elif matched_count > 0:
                report.questions_partially_covered += 1
            else:
                report.questions_zero_covered += 1

        if report.total_gold_provisions > 0:
            report.coverage_rate_provisions = (
                report.matched_provisions / report.total_gold_provisions
            )
        if report.total_questions > 0:
            report.coverage_rate_questions = report.questions_fully_covered / report.total_questions

        report.missing_provision_ids = list(set(missing_ids))
        return report

    def export_gold_eval(
        self,
        qa_dev_items: list[QAEvaluationItem],
        qa_test_items: list[QAEvaluationItem],
        output_dir: str | Path = "data/gold_eval",
    ) -> tuple[Path, Path | None]:
        """Xuất danh sách QA items ra data/gold_eval/qa_dev.jsonl và qa_test.jsonl.

        Args:
            qa_dev_items: Danh sách câu hỏi dev.
            qa_test_items: Danh sách câu hỏi test.
            output_dir: Thư mục đích.

        Returns:
            Tuple chứa (qa_dev_path, qa_test_path).
        """
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        dev_path = out_path / "qa_dev.jsonl"
        with open(dev_path, "w", encoding="utf-8") as f:
            for item in qa_dev_items:
                f.write(item.model_dump_json() + "\n")

        test_path: Path | None = None
        if qa_test_items:
            test_path = out_path / "qa_test.jsonl"
            with open(test_path, "w", encoding="utf-8") as f:
                for item in qa_test_items:
                    f.write(item.model_dump_json() + "\n")

        logger.info(
            f"Đã lưu tập đánh giá tại: dev={dev_path} ({len(qa_dev_items)} items), "
            f"test={test_path} ({len(qa_test_items)} items)"
        )
        return dev_path, test_path
