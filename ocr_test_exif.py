import sys
import subprocess
import tempfile
from pathlib import Path
from PIL import Image, ImageOps

img_path = Path("/Users/prakashthatikunta/Documents/Health/Health prakash/Lab tests/Dexa scans/F02C364D-7AA1-45E2-93BA-C454B059A56D_1_105_c.jpeg")
if not img_path.exists():
    print(f"Error: JPEG not found at {img_path}")
    sys.exit(1)

# Open image and transpose EXIF orientation
img = Image.open(img_path)
exif = img.getexif()
orientation = exif.get(274) if exif else None
print(f"EXIF Orientation tag: {orientation}")

transposed_img = ImageOps.exif_transpose(img)

# Save to temp PNG file for OCR
with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f_out:
    temp_path = f_out.name
transposed_img.save(temp_path, format="PNG")

output_base = temp_path + ".ocr"
try:
    res = subprocess.run(
        ["tesseract", temp_path, output_base],
        capture_output=True, text=True
    )
    out_txt = Path(f"{output_base}.txt")
    if out_txt.exists():
        text = out_txt.read_text(encoding="utf-8", errors="ignore").strip()
        print("\n=== EXTRACTED TEXT FROM EXIF-TRANSPOSED IMAGE ===")
        print(text[:2000])
        out_txt.unlink(missing_ok=True)
    else:
        print("Tesseract failed. Stderr:")
        print(res.stderr)
finally:
    Path(temp_path).unlink(missing_ok=True)
