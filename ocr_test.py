import sys
import subprocess
import tempfile
from pathlib import Path
from pypdf import PdfReader

pdf_path = Path("/Users/prakashthatikunta/Documents/Health/Health prakash/Lab tests/Dexa scans/Dexa-05-29-2026.pdf")
reader = PdfReader(str(pdf_path))
page = reader.pages[0]

print(f"Number of images on page 1: {len(getattr(page, 'images', []))}")
for idx, image_file in enumerate(getattr(page, 'images', [])):
    print(f"\n--- IMAGE {idx+1} ---")
    data = image_file.data
    
    # Save image to temp file
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f_in:
        f_in.write(data)
        input_name = f_in.name
        
    output_base = input_name + ".ocr"
    try:
        # Run tesseract directly on raw image bytes
        res = subprocess.run(
            ["tesseract", input_name, output_base],
            capture_output=True, text=True
        )
        out_txt = Path(f"{output_base}.txt")
        if out_txt.exists():
            text = out_txt.read_text(encoding="utf-8", errors="ignore").strip()
            print("Extracted text (first 1000 chars):")
            print(text[:1000])
            out_txt.unlink(missing_ok=True)
        else:
            print("Tesseract did not produce output. Error details:")
            print(res.stderr)
    except Exception as e:
        print(f"Exception during tesseract run: {e}")
    finally:
        Path(input_name).unlink(missing_ok=True)
