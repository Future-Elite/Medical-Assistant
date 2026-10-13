"""hisgen —— 上海地区 Synthea 患者数据生成。

分层：
    ir/        层 1 产物：临床路径 IR 的 pydantic 模型与加载
    terminology/ 编码体系与术语白名单
    compiler/  层 2：确定性编译器，IR -> GMF
    llm/       层 1：指南 -> IR（唯一使用大模型的地方）
    runner/    层 4：调用 run_synthea
    export/    层 5：FHIR -> 关系型多表
"""

__version__ = "0.1.0"

#: Synthea 源码根目录。可用环境变量 SYNTHEA_HOME 覆盖。
import os as _os
from pathlib import Path as _Path

SYNTHEA_HOME = _Path(
    _os.environ.get("SYNTHEA_HOME", "F:/githubProject/synthea-master/synthea-master")
)

#: 本项目根目录（HISdata/），即 src/ 的上一级。
PROJECT_ROOT = _Path(__file__).resolve().parents[2]

__all__ = ["__version__", "SYNTHEA_HOME", "PROJECT_ROOT"]