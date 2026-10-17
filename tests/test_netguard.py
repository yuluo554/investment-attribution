"""零上传静态门禁：必须同时有阳性和阴性对照，否则是假门。"""

import os

from context import REPO_ROOT, SRC_ROOT

from investment_attribution.netguard import FORBIDDEN_TOP_LEVEL, scan_network_imports


def test_package_sources_have_no_network_imports():
    violations = scan_network_imports(SRC_ROOT)
    assert violations == []


def test_positive_control_detects_plain_import(tmp_path):
    (tmp_path / "bad.py").write_text("import socket\n", encoding="utf-8")
    found = scan_network_imports(str(tmp_path))
    assert [(line, name) for _, line, name in found] == [(1, "socket")]


def test_positive_control_detects_from_import(tmp_path):
    (tmp_path / "bad.py").write_text("from urllib.request import urlopen\n", encoding="utf-8")
    found = scan_network_imports(str(tmp_path))
    assert [(line, name) for _, line, name in found] == [(1, "urllib")]


def test_positive_control_detects_import_inside_function(tmp_path):
    (tmp_path / "bad.py").write_text(
        "def fetch():\n    import http.client\n", encoding="utf-8"
    )
    assert scan_network_imports(str(tmp_path))


def test_negative_control_allows_stdlib_non_network(tmp_path):
    (tmp_path / "ok.py").write_text(
        "import json\nfrom pathlib import Path\n", encoding="utf-8"
    )
    assert scan_network_imports(str(tmp_path)) == []


def test_forbidden_list_contains_core_network_modules():
    assert {"socket", "ssl", "urllib", "http", "requests"} <= FORBIDDEN_TOP_LEVEL
