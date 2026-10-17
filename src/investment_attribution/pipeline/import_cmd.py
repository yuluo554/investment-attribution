"""``import`` 子命令编排。

退出码语义（K1）：

- ``0``：全部文件入库、无行级失败、无待确认账户；
- ``1``：有行级失败或有未登记账户（数据可用但有需要人看的东西）；
- ``2``：整份文件不可用（缺列 / 空文件 / 编码探测失败 / 仅表头）。

**整份文件不可用时不写任何产物**：留下半份流水比什么都不留更危险。
"""

from ..exit_codes import EXIT_DEGRADED, EXIT_OK
from ..ingest import import_files
from ..paths import resolve_data_dir


def run_import(
    inputs,
    data_dir=None,
    out_dir=None,
    accounts_path=None,
    ledger_path=None,
    errors_path=None,
    imported_at=None,
):
    """执行导入，返回 ``ImportResult``（含退出码）。"""
    resolved = resolve_data_dir(data_dir)
    return import_files(
        inputs,
        resolved,
        out_dir=out_dir,
        accounts_path=accounts_path,
        ledger_path=ledger_path,
        errors_path=errors_path,
        imported_at=imported_at,
    )


def exit_code_for(result):
    return EXIT_DEGRADED if result.degraded else EXIT_OK
