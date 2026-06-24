import sys
import types

from fastapi import HTTPException

from web.backend.api.screenshot_import import _ocr_to_lines


def test_ocr_to_lines_bad_image_returns_400(monkeypatch):
    fake_rapidocr = types.ModuleType("rapidocr_onnxruntime")

    class _FakeRapidOCR:
        def __call__(self, img):
            return [], None

    fake_rapidocr.RapidOCR = _FakeRapidOCR
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", fake_rapidocr)

    fake_np = types.ModuleType("numpy")
    fake_np.uint8 = object()
    fake_np.frombuffer = lambda img_bytes, dtype: img_bytes
    monkeypatch.setitem(sys.modules, "numpy", fake_np)

    fake_cv2 = types.ModuleType("cv2")
    fake_cv2.IMREAD_COLOR = 1
    fake_cv2.imdecode = lambda arr, flag: None
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)

    try:
        _ocr_to_lines(b"not-a-real-image")
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "无法解码图片" in exc.detail
    else:
        raise AssertionError("expected HTTPException for invalid image bytes")
