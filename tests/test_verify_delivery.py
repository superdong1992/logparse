"""交付覆盖率检查应跨平台识别核心代码，不能把空集合当作满分。"""

import pytest

from scripts.verify_delivery import core_line_percent


@pytest.mark.parametrize("separator", ["/", "\\"])
def test_core_coverage_handles_report_path_separator(separator):
    files = {
        "backend/application/service.py".replace("/", separator): {
            "summary": {"covered_lines": 80, "num_statements": 100}
        },
        "backend/contracts/runtime.py".replace("/", separator): {
            "summary": {"covered_lines": 10, "num_statements": 10}
        },
        "backend/extensions/parser.py".replace("/", separator): {
            "summary": {"covered_lines": 100, "num_statements": 100}
        },
    }
    assert core_line_percent(files) == pytest.approx(90 / 110 * 100)


def test_empty_core_coverage_is_not_reported_as_success():
    assert core_line_percent({}) == 0
