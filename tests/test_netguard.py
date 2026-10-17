"""零上传门禁：静态（AST import）与运行期（审计钩子）两层，都要有正负对照。"""

import os
import socket

import pytest

from context import REPO_ROOT, SRC_ROOT

from investment_attribution.netguard import (
    FORBIDDEN_TOP_LEVEL,
    NetworkAccessBlocked,
    armed_runtime_probe,
    runtime_probe,
    scan_network_imports,
)


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


# ---------------------------------------------------------------------------
# 运行期探针（四层审计第三层）：真 socket 的正/负对照
# ---------------------------------------------------------------------------


def test_runtime_probe_blocks_real_socket_and_records():
    probe = runtime_probe()
    probe.reset()
    with armed_runtime_probe():
        with pytest.raises(NetworkAccessBlocked) as excinfo:
            socket.socket()
    assert excinfo.value.event.startswith("socket.")
    assert probe.attempts, "拦截了却没有记账"
    assert probe.attempts[0].startswith("socket.")


def test_runtime_probe_lets_traffic_through_when_disarmed():
    """阴性对照：解除武装后同一个调用必须放行——否则探针把正常程序也拦了。"""
    probe = runtime_probe()
    probe.reset()
    with armed_runtime_probe():
        pass
    sock = socket.socket()
    sock.close()
    assert probe.attempts == []


def test_runtime_probe_restores_arm_state_after_exception():
    probe = runtime_probe()
    probe.reset()
    with pytest.raises(RuntimeError):
        with armed_runtime_probe():
            raise RuntimeError("boom")
    assert probe.armed is False
    sock = socket.socket()
    sock.close()


def test_runtime_probe_only_blocks_network_namespaces():
    """非网络审计事件必须透传（探针不做"一刀切"）。"""
    import sys

    with armed_runtime_probe():
        sys.audit("open", str(REPO_ROOT / "README.md"), "r", 0)

