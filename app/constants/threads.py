
VIDEO_FORMAT = "video"
UNKNOWN_MEDIA_FORMAT = "Unknown"

ALLOWED_VIDEO_FORMAT = ("mp4", "mov")
ALLOWED_IMAGE_FORMAT = (
    'png',
    'jpg',
    'jpeg',
    # The frontend compresses images to WebP before uploading, so the stored
    # reference (and the direct-to-bucket upload) is a .webp object.
    'webp',
)

ALLOWED_MEDIA_FORMATS = ALLOWED_VIDEO_FORMAT + ALLOWED_IMAGE_FORMAT

# Content types accepted for the presigned direct-to-bucket upload, mapped to
# the extension stored in the object key. Kept a strict subset of
# ALLOWED_MEDIA_FORMATS so the presign can only ever sign a known media type.
UPLOAD_CONTENT_TYPE_EXT = {
    "image/webp": "webp",
    "image/png": "png",
    "image/jpeg": "jpg",
    "video/mp4": "mp4",
    # QuickTime / iPhone .mov (standard MIME for .mov is video/quicktime).
    "video/quicktime": "mov",
}
