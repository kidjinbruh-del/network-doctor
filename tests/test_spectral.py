from __future__ import annotations

import unittest

from network_doctor.spectral import parse_spectral_scan


class SpectralParserTests(unittest.TestCase):
    def test_text_table_s_columns(self) -> None:
        raw = """
        FREQ USE S10 S20 S30
        2412 88% -90 -92 -94
        2437 50% -96 -98 -100
        """
        result = parse_spectral_scan(raw)
        self.assertEqual(result.samples, 6)
        self.assertEqual(result.noise_floor_avg, -95.0)
        self.assertEqual(result.method, "text-s-columns")

    def test_text_table_noise_column(self) -> None:
        raw = """
        FREQ USE NOISE
        2412 70% -92
        2437 30% -96
        """
        result = parse_spectral_scan(raw)
        self.assertEqual(result.samples, 2)
        self.assertEqual(result.noise_floor_avg, -94.0)
        self.assertEqual(result.method, "text-noise-column")

    def test_key_value_noise(self) -> None:
        result = parse_spectral_scan("noise-floor: -97.5 dBm")
        self.assertEqual(result.samples, 1)
        self.assertEqual(result.noise_floor_avg, -97.5)

    def test_dictionary_rows(self) -> None:
        result = parse_spectral_scan(
            [
                {"freq": 2412, "s10": -88, "s20": -90},
                {"freq": 2437, "s10": -92, "s20": -94},
            ]
        )
        self.assertEqual(result.samples, 4)
        self.assertEqual(result.noise_floor_avg, -91.0)
        self.assertEqual(result.method, "dict-s-columns")

    def test_dictionary_noise_floor(self) -> None:
        result = parse_spectral_scan(
            [
                {"frequency": 2412, "noise-floor": "-101"},
                {"frequency": 2437, "noise-floor": "-103"},
            ]
        )
        self.assertEqual(result.samples, 2)
        self.assertEqual(result.noise_floor_avg, -102.0)
        self.assertEqual(result.method, "dict-noise")

    def test_bytes_keys_and_values(self) -> None:
        result = parse_spectral_scan([{b"noise-floor": b"-99"}])
        self.assertEqual(result.noise_floor_avg, -99.0)

    def test_empty_input(self) -> None:
        result = parse_spectral_scan("")
        self.assertIsNone(result.noise_floor_avg)
        self.assertEqual(result.samples, 0)
        self.assertEqual(result.method, "none")


if __name__ == "__main__":
    unittest.main()
