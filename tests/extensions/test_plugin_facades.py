from backend.extensions.mechanisms.module1 import Module1Plugin as ExtensionModule1
from backend.extensions.mechanisms.module2 import Module2Plugin as ExtensionModule2
from backend.extensions.products.current.parser import ParserPlugin as ExtensionParser
from backend.extensions.products.current.scanner import ScannerPlugin as ExtensionScanner
from backend.plugins.default.parser import ParserPlugin as LegacyParser
from backend.plugins.default.scanner import ScannerPlugin as LegacyScanner
from backend.plugins.mechanisms.module1 import Module1Plugin as LegacyModule1
from backend.plugins.mechanisms.module2 import Module2Plugin as LegacyModule2
from backend.extensions.products.current.artifacts import MechOutputWriter as ExtensionWriter
from backend.parsing.output_writer import MechOutputWriter as LegacyWriter
from backend.extensions.products.current.metadata import MetadataGenerator as ExtensionMetadata
from backend.extensions.products.current.result_serializer import result_to_dict as extension_result_to_dict
from backend.extensions.products.current.query import ResultQueryService as ExtensionQuery
from backend.extensions.products.current.dfx import build_dfx_output as extension_build_dfx_output
from backend.metadata import MetadataGenerator as LegacyMetadata
from backend.query import ResultQueryService as LegacyQuery
from backend.dfx import build_dfx_output as legacy_build_dfx_output
from backend.result_serializer import result_to_dict as legacy_result_to_dict


def test_legacy_product_plugin_paths_are_facades() -> None:
    assert LegacyParser is ExtensionParser
    assert LegacyScanner is ExtensionScanner


def test_legacy_mechanism_paths_are_facades() -> None:
    assert LegacyModule1 is ExtensionModule1
    assert LegacyModule2 is ExtensionModule2


def test_legacy_artifact_projection_path_is_a_facade() -> None:
    assert LegacyWriter is ExtensionWriter
    assert LegacyMetadata is ExtensionMetadata
    assert legacy_result_to_dict is extension_result_to_dict


def test_legacy_query_and_dfx_paths_are_facades() -> None:
    assert LegacyQuery is ExtensionQuery
    assert legacy_build_dfx_output is extension_build_dfx_output
