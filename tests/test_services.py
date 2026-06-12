import io
from PIL import Image as PILImage
from app.services.image_service import image_service
from app.services.moderation_service import moderation_service

def test_image_thumbnail_generation():
    # 1. Create solid color test image
    img = PILImage.new("RGB", (600, 400), color="blue")
    output = io.BytesIO()
    img.save(output, format="JPEG")
    img_bytes = output.getvalue()
    
    # 2. Resize via service
    thumb_bytes = image_service.generate_thumbnail(img_bytes, max_width=300)
    
    # 3. Verify target dimensions
    thumb = PILImage.open(io.BytesIO(thumb_bytes))
    assert thumb.width == 300
    assert thumb.height == 200

def test_moderation_nsfw_detection():
    # 1. Create a safe solid blue image
    safe_img = PILImage.new("RGB", (100, 100), color="blue")
    output = io.BytesIO()
    safe_img.save(output, format="JPEG")
    assert not moderation_service.check_nsfw(output.getvalue())
    
    # 2. Create a high-density skin color image (R=240, G=180, B=140 matches human skin)
    nsfw_img = PILImage.new("RGB", (100, 100), color=(240, 180, 140))
    output = io.BytesIO()
    nsfw_img.save(output, format="JPEG")
    assert moderation_service.check_nsfw(output.getvalue())
