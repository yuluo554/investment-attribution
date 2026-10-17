"""领域异常。

所有领域异常继承 ValueError（或 CLI 明确捕获的基类），保证 CLI 的
``except`` 抓得住、能转成既定退出码，而不是逃逸成 traceback。
"""

from .exit_codes import EXIT_INPUT, EXIT_STAGE_NOT_READY


class IAError(ValueError):
    """所有领域异常的基类，带机读 ``code`` 字段。"""

    code = "IA_ERROR"
    exit_code = EXIT_INPUT

    def __init__(self, message, *, code=None):
        super().__init__(message)
        if code is not None:
            self.code = code


class UsageError(IAError):
    """参数语义错误：必需参数缺失、取值不在白名单。"""

    code = "USAGE_ERROR"


class InputError(IAError):
    """输入不可用：文件不存在、编码探测全失败、形态不可解析。"""

    code = "INPUT_ERROR"


class DataError(IAError):
    """随包数据契约损坏：manifest 缺失/字段不全/冻结产物不在位。"""

    code = "DATA_ERROR"


class StageNotReady(IAError):
    """模块尚未落地，显式拦截，不输出任何伪造结果。"""

    code = "STAGE_NOT_READY"
    exit_code = EXIT_STAGE_NOT_READY
