"""Attachments — polymorphic file upload/download stored on Cloudinary.

Supported entity_type values: milestone | task | subtask | activity | report

Public-ID stored in stored_filename column:
  wbs/attachments/<entity_type>/<uuid>
The secure_url is reconstructed at read-time via build_url().
"""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from sqlalchemy.orm import Session
from typing import Optional
import uuid
from app.db.database import get_db
from app.models.models import Attachment, User
from app.core.deps import get_current_user
from app.utils.cloudinary_helper import upload_file, delete_file, build_url

router = APIRouter(prefix="/attachments", tags=["Attachments"])

VALID_ENTITY_TYPES = {"milestone", "task", "subtask", "activity", "report"}


def _build(a: Attachment):
    return {
        "id": a.id,
        "entity_type": a.entity_type,
        "entity_id": a.entity_id,
        "original_filename": a.original_filename,
        "stored_filename": a.stored_filename,
        "file_size": a.file_size,
        "mime_type": a.mime_type,
        "uploaded_by": a.uploaded_by,
        "uploader_name": a.uploader.name if a.uploader else None,
        "created_at": a.created_at,
        "url": build_url(a.stored_filename) if a.stored_filename else None,
    }


@router.get("")
def list_attachments(
    entity_type: str = Query(...),
    entity_id: int = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = (
        db.query(Attachment)
        .filter_by(entity_type=entity_type, entity_id=entity_id)
        .order_by(Attachment.created_at)
        .all()
    )
    return [_build(a) for a in rows]


@router.post("/upload")
async def upload_attachment(
    entity_type: str = Form(...),
    entity_id: int = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if entity_type not in VALID_ENTITY_TYPES:
        raise HTTPException(400, f"entity_type must be one of: {', '.join(sorted(VALID_ENTITY_TYPES))}")

    public_id = f"wbs/attachments/{entity_type}/{uuid.uuid4().hex}"
    file_bytes = await file.read()

    result = upload_file(file_bytes, public_id)

    a = Attachment(
        entity_type=entity_type,
        entity_id=entity_id,
        original_filename=file.filename or public_id,
        stored_filename=result["public_id"],
        file_size=result.get("bytes", len(file_bytes)),
        mime_type=file.content_type,
        uploaded_by=current_user.id,
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return _build(a)


@router.delete("/{attachment_id}")
def delete_attachment(
    attachment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    a = db.query(Attachment).filter_by(id=attachment_id).first()
    if not a:
        raise HTTPException(404, "Attachment not found")

    delete_file(a.stored_filename)

    db.delete(a)
    db.commit()
    return {"status": "deleted"}
