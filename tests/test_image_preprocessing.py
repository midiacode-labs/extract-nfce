import tempfile
import unittest

import cv2
import numpy as np

from services.invoice_service import InvoiceExtractionService


class _FakeTextractClient:
    def __init__(self):
        self.document_bytes = None

    def analyze_expense(self, Document):
        self.document_bytes = Document["Bytes"]
        return {"ExpenseDocuments": []}


class ImagePreprocessingTests(unittest.TestCase):
    def test_preprocess_image_bytes_returns_png_payload(self):
        image = np.full((320, 480, 3), 255, dtype=np.uint8)
        cv2.putText(
            image,
            "DESTINATARIO/REMETENTE",
            (10, 120),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.9,
            (0, 0, 0),
            2,
            cv2.LINE_AA,
        )
        success, encoded = cv2.imencode(".jpg", image)
        self.assertTrue(success)

        service = InvoiceExtractionService(enable_preprocessing=True)
        processed_bytes = service.preprocess_image_bytes(encoded.tobytes())

        self.assertGreater(len(processed_bytes), 0)
        self.assertTrue(processed_bytes.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_analyze_image_sends_preprocessed_bytes_to_textract(self):
        image = np.full((300, 500, 3), 255, dtype=np.uint8)
        cv2.putText(
            image,
            "MARLENE DE LIMA",
            (20, 150),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (0, 0, 0),
            2,
            cv2.LINE_AA,
        )
        success, encoded = cv2.imencode(".jpg", image)
        self.assertTrue(success)
        original_bytes = encoded.tobytes()

        with tempfile.NamedTemporaryFile(suffix=".jpg") as temp_file:
            temp_file.write(original_bytes)
            temp_file.flush()

            fake_client = _FakeTextractClient()
            service = InvoiceExtractionService(enable_preprocessing=True)
            service.create_textract_client = lambda: fake_client

            response = service.analyze_image(temp_file.name)

        self.assertEqual(response, {"ExpenseDocuments": []})
        self.assertIsNotNone(fake_client.document_bytes)
        self.assertNotEqual(fake_client.document_bytes, original_bytes)
        self.assertTrue(fake_client.document_bytes.startswith(b"\x89PNG\r\n\x1a\n"))


if __name__ == "__main__":
    unittest.main()
