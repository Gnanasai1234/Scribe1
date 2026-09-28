"""Transactional document repository used by API routes."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from .connection import SessionLocal
from .models import (AudioRecord, DocumentChunkRecord, DocumentPageRecord, DocumentRecord,
                     QuestionAnswerRecord, SummaryClaimRecord, SummaryRecord, TranslationRecord,
                     ValidationResultRecord)


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def save_document(doc: dict[str, Any]) -> None:
    """Atomically upsert a document snapshot and its normalized child records."""
    doc_id = str(doc["id"])
    created = datetime.fromisoformat(str(doc.get("created_at") or datetime.now(timezone.utc).isoformat()))
    if created.tzinfo:
        created = created.astimezone(timezone.utc).replace(tzinfo=None)
    pages = doc.get("pages", []) or []
    chunks = doc.get("chunks", []) or []
    audio = doc.get("audio_timings") or {}
    result = doc.get("result")
    with SessionLocal.begin() as session:
        row = session.get(DocumentRecord, doc_id)
        if row is None:
            row = DocumentRecord(id=doc_id, name=doc.get("name", "Document"), text=doc.get("text", ""),
                                 language=doc.get("language", "und"), status=doc.get("status", "UPLOADED"),
                                 created_at=created)
            session.add(row)
        row.name = doc.get("name", "Document")
        row.text = doc.get("text", "")
        row.language = doc.get("language", "und")
        row.status = doc.get("status", "UPLOADED")
        row.ocr_records = _jsonable(doc.get("ocr_records", []))
        row.ocr_warnings = _jsonable(doc.get("ocr_warnings", []))
        row.ocr_detected = bool(doc.get("ocr_detected"))
        row.options = _jsonable(doc.get("options")) if doc.get("options") is not None else None
        row.evaluation = _jsonable(doc.get("evaluation")) if doc.get("evaluation") is not None else None
        row.reference_summary = doc.get("reference_summary")
        row.dataset = _jsonable(doc.get("dataset")) if doc.get("dataset") is not None else None
        row.depth_comparison = _jsonable(doc.get("depth_comparison")) if doc.get("depth_comparison") is not None else None
        row.audio_timings = _jsonable(audio) if audio else None
        row.pages.clear()
        for p in pages:
            row.pages.append(DocumentPageRecord(page_number=int(p.get("page", p.get("page_number", 0)) or 0),
                                                text=p.get("text", ""), ocr_data=_jsonable(p.get("ocr")) if p.get("ocr") else None))
        row.chunks.clear()
        for index, chunk in enumerate(chunks, 1):
            row.chunks.append(DocumentChunkRecord(
                source_chunk_id=str(chunk.get("id") or f"chunk_{index:03d}"), chunk_number=int(chunk.get("chunk_number", index)),
                text=chunk.get("text", ""), page_number=chunk.get("page"), section=chunk.get("section"),
                language=chunk.get("language"), source=chunk.get("source"),
                contains_ocr=bool(chunk.get("contains_ocr")), ocr_confidence=chunk.get("ocr_confidence")))
        row.summaries.clear()
        row.validations.clear()
        row.translations.clear()
        if result:
            summary = SummaryRecord(summary_text=result.get("executive_summary", ""), language=result.get("language", doc.get("language", "und")),
                                    mode=result.get("summary_mode", result.get("depth", "standard")), created_at=datetime.now(timezone.utc).replace(tzinfo=None),
                                    result_data=_jsonable(result))
            for index, claim in enumerate(result.get("claims", []) or [], 1):
                summary.claims.append(SummaryClaimRecord(ordinal=index, claim=claim.get("claim", ""), category=claim.get("category"),
                    status=claim.get("status"), evidence=claim.get("evidence"), confidence=claim.get("confidence"), sources=_jsonable(claim.get("sources", []))))
            row.summaries.append(summary)
            quality = result.get("quality", {}) or {}
            if result.get("validation") or result.get("claims"):
                row.validations.append(ValidationResultRecord(status=result.get("validation_status", result.get("status", "unavailable")),
                    groundedness_score=quality.get("groundedness"), created_at=datetime.now(timezone.utc).replace(tzinfo=None)))
            translations = result.get("translations", {})
            if isinstance(translations, dict):
                for lang, translated in translations.items():
                    if isinstance(translated, str):
                        row.translations.append(TranslationRecord(source_language=doc.get("language", "und"), target_language=lang,
                            translated_text=translated, created_at=datetime.now(timezone.utc).replace(tzinfo=None)))
            translated_summary = result.get("executive_summary", "")
            result_language = result.get("language", doc.get("language", "und"))
            if translated_summary and result_language != doc.get("language", "und"):
                row.translations.append(TranslationRecord(source_language=doc.get("language", "und"), target_language=result_language,
                    translated_text=translated_summary, created_at=datetime.now(timezone.utc).replace(tzinfo=None)))
        row.audio_files.clear()
        if audio:
            row.audio_files.append(AudioRecord(language=audio.get("language", row.language), voice_id=audio.get("voice_id", "browser-default"),
                provider=audio.get("provider", "browser"), file_path=audio.get("file_path"), timings=_jsonable(audio.get("timings", [])),
                narration_text=audio.get("text"), fallback_used=bool(audio.get("fallback_used")),
                created_at=datetime.now(timezone.utc).replace(tzinfo=None)))


def get_document(document_id: str) -> dict[str, Any] | None:
    with SessionLocal() as session:
        stmt = select(DocumentRecord).where(DocumentRecord.id == document_id).options(
            selectinload(DocumentRecord.pages), selectinload(DocumentRecord.chunks), selectinload(DocumentRecord.summaries),
            selectinload(DocumentRecord.audio_files))
        row = session.scalar(stmt)
        if row is None:
            return None
        summary = max(row.summaries, key=lambda s: s.id or 0, default=None)
        created = row.created_at.replace(tzinfo=timezone.utc).isoformat()
        return {
            "id": row.id, "name": row.name, "text": row.text, "language": row.language, "status": row.status,
            "created_at": created, "pages": [{"page": p.page_number, "text": p.text, **({"ocr": p.ocr_data} if p.ocr_data else {})} for p in row.pages],
            "chunks": [{"id": c.source_chunk_id, "documentId": row.id, "text": c.text, "page": c.page_number, "section": c.section,
                        "language": c.language, "chunk_number": c.chunk_number, "source": c.source, "contains_ocr": c.contains_ocr,
                        "ocr_confidence": c.ocr_confidence} for c in row.chunks],
            "ocr_records": row.ocr_records or [], "ocr_warnings": row.ocr_warnings or [], "ocr_detected": row.ocr_detected,
            "options": row.options, "evaluation": row.evaluation, "reference_summary": row.reference_summary,
            "dataset": row.dataset, "depth_comparison": row.depth_comparison,
            "result": summary.result_data if summary else None, "audio_timings": row.audio_timings,
        }


def list_documents() -> list[dict[str, Any]]:
    with SessionLocal() as session:
        rows = session.scalars(select(DocumentRecord).options(selectinload(DocumentRecord.pages),
            selectinload(DocumentRecord.chunks)).order_by(DocumentRecord.created_at.desc())).all()
        return [{"id": row.id, "name": row.name, "text": row.text, "language": row.language, "status": row.status,
                 "created_at": row.created_at.replace(tzinfo=timezone.utc).isoformat(),
                 "pages": [{"page": p.page_number, "text": p.text} for p in row.pages],
                 "chunks": [{"id": c.source_chunk_id} for c in row.chunks],
                 "ocr_detected": row.ocr_detected, "ocr_warnings": row.ocr_warnings or [], "result": None}
                for row in rows]


def database_is_connected() -> bool:
    from .connection import engine
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def save_question(document_id: str, question: str, answer: dict[str, Any], language: str) -> None:
    with SessionLocal.begin() as session:
        session.add(QuestionAnswerRecord(document_id=document_id, question=question,
            answer=answer.get("answer", ""), language=language, sources=_jsonable(answer.get("sources", [])),
            created_at=datetime.now(timezone.utc).replace(tzinfo=None)))
