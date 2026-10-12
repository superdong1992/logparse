"""产品解析器报告的错误应进入正式诊断，不能被误报为完整成功。"""

from pathlib import Path

import pytest

from backend.application.configuration import load_config_file
from backend.contracts.runtime import Diagnostic, ParseRequest
from backend.extensions.products.current.engine import CurrentProductParseEngine


@pytest.mark.parametrize("structured", [False, True])
def test_product_parser_errors_mark_run_partial(tmp_path, monkeypatch, structured):
    source = tmp_path / "single.log"
    source.write_text("synthetic input\n", encoding="utf-8")
    message = "合成产品字段不完整"

    class Discovery:
        def discover(self, workspace):
            return [], []

    class Parser:
        last_mechanism_diagnostics = (
            (Diagnostic("LP_CUSTOM_PRODUCT_ERROR", message),) if structured else ()
        )

        def parse(self, result):
            result.errors.extend([message, message])

    config = load_config_file(Path(__file__).parents[1] / "config.yaml")
    engine = CurrentProductParseEngine(config)
    monkeypatch.setattr(engine, "_load_plugins", lambda *_: (Discovery(), Parser()))
    outcome = engine.execute(ParseRequest(source, tmp_path / "out"))
    assert outcome.status == "partial"
    assert len(outcome.diagnostics) == 1
    assert outcome.diagnostics[0].message == message
    assert outcome.diagnostics[0].code == (
        "LP_CUSTOM_PRODUCT_ERROR" if structured else "LP_PARSE_PARTIAL"
    )
