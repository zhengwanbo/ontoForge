import unittest

from app.services.source_data_service import SourceDataService


class SourceTableFilteringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = SourceDataService.__new__(SourceDataService)

    def test_filters_oracle_internal_tables_from_business_catalog(self) -> None:
        self.assertFalse(self.service._is_business_relevant_table("DR$ONTO_IDX$I"))
        self.assertFalse(self.service._is_business_relevant_table("MLOG$_ORDER_FACT"))
        self.assertFalse(self.service._is_business_relevant_table("BIN$XYZ123"))
        self.assertFalse(self.service._is_business_relevant_table("SYS_EXPORT_FULL_01"))

    def test_keeps_normal_business_tables(self) -> None:
        self.assertTrue(self.service._is_business_relevant_table("ORDER_FACT"))
        self.assertTrue(self.service._is_business_relevant_table("DIM_STORE"))
        self.assertTrue(self.service._is_business_relevant_table("TB_DEFECT_RECORD"))

    def test_mapping_candidate_reuses_business_filter(self) -> None:
        self.assertFalse(self.service._is_mapping_candidate_table("DR$TEXT_IDX$K"))
        self.assertTrue(self.service._is_mapping_candidate_table("PRODUCT_TRACE"))


if __name__ == "__main__":
    unittest.main()
