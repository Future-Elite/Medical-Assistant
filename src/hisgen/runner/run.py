"""调用 Synthea 生成数据。

在本项目里，**所有本地化都靠命令行开关完成**，不改 Java 逻辑：

  ``-c shanghai/shanghai.properties``  覆盖任意 synthea.properties 项
  ``-d modules``                        加载自研模块目录
  ``-m "sh_*"``                         模块白名单 —— 官方 107 个美国模块全关
  ``--config*=value``                   单点覆盖

路径一律转成正斜杠：run_synthea.bat 把参数交给 Groovy，
反斜杠会被当作转义字符吃掉（原 data.py 踩过的坑，这里保留这个修复）。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .. import PROJECT_ROOT, SYNTHEA_HOME


class SyntheaNotFound(FileNotFoundError):
    """找不到 run_synthea 可执行文件。"""


#: 按平台优先尝试的启动器文件名。
#:
#: Synthea 源码里同时有 ``run_synthea``（POSIX shell）和 ``run_synthea.bat``，
#: 两个都存在。Windows 上靠 ``PATHEXT`` 恰好会挑中 .bat，但那不是我们能保证的 ——
#: 显式按平台选，免得某天挑中了那个 POSIX 脚本，然后 CreateProcess 报
#: "不是有效的 Win32 应用程序"。
_LAUNCHER_NAMES = (
    ("run_synthea.bat", "run_synthea.cmd", "run_synthea")
    if os.name == "nt"
    else ("run_synthea", "run_synthea.sh")
)


def find_synthea(base_dir: str | Path | None = None) -> Path:
    """定位 run_synthea 启动器。"""
    base = Path(base_dir or SYNTHEA_HOME).resolve()
    if not base.exists():
        raise SyntheaNotFound(
            f"Synthea 目录不存在：{base}\n"
            "请设置环境变量 SYNTHEA_HOME 指向 Synthea 源码根目录。"
        )
    for name in _LAUNCHER_NAMES:
        candidate = base / name
        if candidate.is_file():
            return candidate
    raise SyntheaNotFound(
        f"未在 {base} 找到任何启动器（试过：{'、'.join(_LAUNCHER_NAMES)}）。"
    )


@dataclass
class RunSpec:
    """一次生成任务的完整参数。"""

    population: int = 100
    seed: int = 12345
    #: Synthea 的「州」参数。上海各数据文件里的州名与之匹配。
    state: str = "上海市"
    city: str | None = None
    age_range: str | None = None          # 形如 "20-40"
    gender: str | None = None             # "M" / "F"
    #: 自定义 properties，覆盖 Synthea 默认配置
    properties: Path = field(default_factory=lambda: PROJECT_ROOT / "shanghai" / "shanghai.properties")
    #: 自研模块目录（-d）
    modules_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "modules")
    #: 模块白名单（-m）。os.pathsep 分隔，可用通配符。
    module_filter: str = "sh_*"
    #: 输出目录。实际数据落在 <output_dir>/output/{fhir,csv}
    output_dir: Path | None = None
    #: 额外 --config 覆盖，形如 {"exporter.fhir.export": "true"}
    config_overrides: dict[str, str] = field(default_factory=dict)
    synthea_home: Path = field(default_factory=lambda: Path(SYNTHEA_HOME))
    timeout_seconds: int = 3600

    def resolved_output_dir(self) -> Path:
        if self.output_dir is not None:
            return Path(self.output_dir).resolve()
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return (PROJECT_ROOT / "output" / "runs" / stamp).resolve()

    def build_command(self, exe: Path, output_dir: Path) -> list[str]:
        cmd = [
            str(exe),
            "--exporter.baseDirectory",
            output_dir.as_posix(),
        ]
        if self.population:
            cmd += ["-p", str(self.population)]
        if self.seed is not None:
            cmd += ["-s", str(self.seed)]
        if self.age_range:
            cmd += ["-a", self.age_range]
        if self.gender:
            cmd += ["-g", self.gender.upper()]
        if self.properties:
            props = Path(self.properties)
            if not props.exists():
                raise FileNotFoundError(f"properties 文件不存在：{props}")
            cmd += ["-c", props.resolve().as_posix()]
        if self.modules_dir:
            mods = Path(self.modules_dir)
            if not mods.exists():
                raise FileNotFoundError(
                    f"模块目录不存在：{mods}\n先跑 python -m hisgen.compiler 生成模块。"
                )
            cmd += ["-d", mods.resolve().as_posix()]
        if self.module_filter:
            cmd += ["-m", self.module_filter]
        for key, value in sorted(self.config_overrides.items()):
            cmd.append(f"--config{key}={value}")
        # 位置参数：州。必须放最后。
        if self.state:
            cmd.append(self.state)
        if self.city:
            cmd.append(self.city)
        return cmd

    def describe(self) -> dict:
        d = asdict(self)
        for key in ("properties", "modules_dir", "output_dir", "synthea_home"):
            d[key] = str(d[key]) if d[key] is not None else None
        return d


@dataclass
class RunResult:
    returncode: int
    output_dir: Path
    fhir_dir: Path
    csv_dir: Path
    command: list[str]
    stdout: str
    stderr: str
    manifest_path: Path | None = None
    #: 请求生成的人数。用来识别"返回码 0 但一个患者都没导出"这种静默失败。
    population_wanted: int = 0

    @property
    def fhir_files(self) -> list[Path]:
        return sorted(self.fhir_dir.glob("*.json")) if self.fhir_dir.exists() else []

    @property
    def patient_files(self) -> list[Path]:
        """只数患者文件。

        ``fhir/`` 里还有 ``hospitalInformation*.json`` / ``practitionerInformation*.json``
        —— 它们**不依赖患者**，患者一个都没导出成功也照样存在。用总数判断成败
        会把"全部患者导出失败"误判成成功（实测发生过：5 个文件里只有 1 个患者）。
        """
        return [
            p
            for p in self.fhir_files
            if not p.name.startswith(("hospitalInformation", "practitionerInformation"))
        ]

    @property
    def records(self) -> tuple[int, int, int] | None:
        """Synthea 自己的汇总：``(total, alive, dead)``；日志里没有就返回 None。

        ``-p N`` 的含义是「**存活** N 人」，不是「产出 N 个文件」。模拟中途死亡的
        患者仍会被导出（Generator 的 CriteriaCheck：死者补生成一个，但记录照导出），
        所以 ``total`` 会大于 N。用文件数去比 N 会得出"多生成了"的错误结论。
        """
        match = re.search(
            r"Records: total=(\d+), alive=(\d+), dead=(\d+)", self.stdout
        )
        return tuple(int(g) for g in match.groups()) if match else None

    def tail(self, n: int = 40) -> str:
        """日志尾部，用于失败诊断。"""
        lines = (self.stdout + "\n" + self.stderr).splitlines()
        return "\n".join(lines[-n:])


def run_synthea(spec: RunSpec, *, stream: bool = True) -> RunResult:
    """执行一次 Synthea 生成。返回结果；``returncode != 0`` 不抛异常，由调用方决定。"""
    exe = find_synthea(spec.synthea_home)
    output_dir = spec.resolved_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = spec.build_command(exe, output_dir)

    if stream:
        print(f"执行命令：{' '.join(cmd)}")
        print(f"工作目录：{spec.synthea_home}")
        print("正在生成数据，请稍候……")

    # run_synthea.bat 内部是裸调用 `gradlew.bat run -Params=...`。cmd.exe 本来
    # 会在当前目录里找批处理，但这台机器设了 NoDefaultCurrentDirectoryInExePath=1，
    # 当前目录被排除在外，于是报 "'gradlew.bat' 不是内部或外部命令"。
    # 把 Synthea 根目录挂到 PATH 最前面即可 —— 不动 Synthea 自己的参数拼装逻辑，
    # 也就不需要维护一份 gradle 调用方式的副本。
    env = os.environ.copy()
    env["PATH"] = str(spec.synthea_home) + os.pathsep + env.get("PATH", "")

    proc = subprocess.run(
        cmd,
        cwd=str(spec.synthea_home),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",  # Windows 上日志可能混 GBK 字节，容错避免解码崩溃
        timeout=spec.timeout_seconds,
    )

    result = RunResult(
        returncode=proc.returncode,
        output_dir=output_dir,
        # ``--exporter.baseDirectory`` 就是基准目录本身，Synthea 直接在它下面
        # 建 ``fhir/`` / ``csv/`` / ``metadata/``，不额外套一层 ``output/``。
        # 写成 <output_dir>/output/fhir 会一个文件都找不到，于是"有文件就算成功"
        # 的兜底判断失效，真实的导出失败被误报成 0 个文件。
        fhir_dir=output_dir / "fhir",
        csv_dir=output_dir / "csv",
        command=cmd,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
        population_wanted=spec.population,
    )

    result.manifest_path = _write_manifest(spec, result, exe)

    if stream:
        if proc.stdout:
            print("---- Synthea 输出 ----")
            print(proc.stdout)
        if proc.stderr:
            print("---- Synthea 错误输出 ----")
            print(proc.stderr)
        if result.fhir_dir.exists():
            records = result.records
            detail = (
                f"存活 {records[1]}、死亡 {records[2]}（共 {records[0]}）"
                if records
                else f"{len(result.patient_files)} 个患者"
            )
            print(f"FHIR 目录：{result.fhir_dir}（{detail}）")
        if result.csv_dir.exists():
            print(f"CSV  目录：{result.csv_dir}")
        print(f"运行清单：{result.manifest_path}")

    return result


def _write_manifest(spec: RunSpec, result: RunResult, exe: Path) -> Path:
    """记录运行元数据，作为数据溯源凭据。

    Synthea 自己也会在 output/metadata/ 下写人口/州/种子，这里再记一份
    我们这边的参数（properties / 模块白名单 / 命令行），补上前者没有的部分。
    """
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "synthea": {
            "home": str(spec.synthea_home),
            "executable": str(exe),
            "version": _synthea_version(spec.synthea_home),
        },
        "spec": spec.describe(),
        "command": result.command,
        "returncode": result.returncode,
        "output_dir": str(result.output_dir),
        "modules_loaded": _modules_in(spec.modules_dir),
    }
    path = result.output_dir / "hisgen-run-manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _synthea_version(home: Path) -> str:
    for candidate in (
        home / "src" / "main" / "resources" / "version.txt",
        home / "version.txt",
    ):
        if candidate.exists():
            return candidate.read_text(encoding="utf-8", errors="replace").strip()
    return "unknown"


def _modules_in(modules_dir: Path) -> list[str]:
    if not modules_dir or not Path(modules_dir).exists():
        return []
    return sorted(p.name for p in Path(modules_dir).glob("*.json"))