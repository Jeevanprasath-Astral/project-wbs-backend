"""Cloudinary helper — centralised upload / delete / URL for all file storage.

All files (PDF, DOCX, images, etc.) are uploaded with resource_type='raw'
so they are served as direct downloads regardless of file type. Images would
also work with resource_type='auto', but keeping everything 'raw' avoids
per-file branching logic and ensures consistent download behaviour.

Public-ID convention:
  attachments/<entity_type>/<uuid>   e.g. attachments/milestone/a1b2c3
  costs/<uuid>_<sanitised_filename>  e.g. costs/a1b2c3_invoice.pdf
"""

import cloudinary
import cloudinary.uploader
import cloudinary.api
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

_configured = False


def _setup():
    global _configured
    if not _configured:
        cloudinary.config(
            cloud_name=settings.CLOUDINARY_CLOUD_NAME,
            api_key=settings.CLOUDINARY_API_KEY,
            api_secret=settings.CLOUDINARY_API_SECRET,
            secure=True,
        )
        _configured = True


def upload_file(file_bytes: bytes, public_id: str) -> dict:
    """Upload bytes to Cloudinary. Returns the full Cloudinary response dict.
    Keys of interest: secure_url, public_id, bytes."""
    _setup()
    result = cloudinary.uploader.upload(
        file_bytes,
        public_id=public_id,
        resource_type="raw",
        overwrite=True,
    )
    return result


def delete_file(public_id: str) -> None:
    """Delete a file from Cloudinary by its public_id. Silently ignores errors."""
    if not public_id:
        return
    try:
        _setup()
        cloudinary.uploader.destroy(public_id, resource_type="raw")
    except Exception as e:
        logger.warning(f"Cloudinary delete failed for {public_id!r}: {e}")


def build_url(public_id: str) -> str:
    """Reconstruct the HTTPS download URL from a stored public_id."""
    _setup()
    url, _ = cloudinary.utils.cloudinary_url(public_id, resource_type="raw", secure=True)
    return url
