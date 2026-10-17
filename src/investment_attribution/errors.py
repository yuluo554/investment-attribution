"""领域异常。

所有领域异常继承 ValueError（或 CLI 明确捕获的基类），保证 CLI 的
``except`` 抓得住、能转成既定退出码，而不是逃逸成 traceback。
"""

from .exit_codes import EXIT_DEGRADED, EXIT_INPUT


class IAError(ValueError):
    """所有领域异常的基类，带机读 ``code`` 字段。"""

    code = "IA_ERROR"
    exit_code = EXIT_INPUT

    def __init__(self, message, *, code=None):
        super().__init__(message)
        if code is not None:
            self.code = code


class DegradedError(IAError):
    """数据可用但结果本身不成立：对应退出码 1（不是 2）。

    与 ``InputError`` 的区别是"输入没毛病，是结论算不出来"——例如 XIRR
    在定义域内无解、拆分守恒被打破。把它单独成类，是为了让退出码 1 的
    语义在异常层就能被读出，而不是靠调用方逐处判断。
    """

    code = "DEGRADED"
    exit_code = EXIT_DEGRADED


class UsageError(IAError):
    """参数语义错误：必需参数缺失、取值不在白名单。"""

    code = "USAGE_ERROR"


class InputError(IAError):
    """输入不可用：文件不存在、编码探测全失败、形态不可解析。"""

    code = "INPUT_ERROR"


class DataError(IAError):
    """随包数据契约损坏：manifest 缺失/字段不全/冻结产物不在位。"""

    code = "DATA_ERROR"
