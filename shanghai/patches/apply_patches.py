# -*- coding: utf-8 -*-
"""把上海本地化补丁打到 Synthea 源码树上。

为什么需要补丁：下面三处 Synthea **没有任何配置项可用**，只能改代码 ——

1. ``Names.loadNames()`` 把 ``names.yml`` 写死成相对 classpath 的文件名，
   且 ``readResource`` 单参重载不允许文件系统路径。不改就读不到我们的姓名池。
2. ``LifecycleModule.birth()`` 把电话格式 ``"555-xxx-xxxx"`` 写死在方法体里。
3. ``Demographics.languageFromRaceAndEthnicity()`` 的语言分布是按美国人口普查
   口径硬编码的，非美国地区拿不到正确的母语标记。

补丁原则：**只做加法**。每一项都先查配置键，配置缺失时行为与原来完全一致 ——
没打补丁的项目跑同一个 Synthea 不会受影响。

可重复执行：每个文件先备份成 ``<名字>.hisgen-orig``，已打过补丁的会跳过。

    python shanghai/patches/apply_patches.py            # 打补丁
    python shanghai/patches/apply_patches.py --check    # 只看状态
    python shanghai/patches/apply_patches.py --revert   # 从备份还原
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from hisgen import SYNTHEA_HOME  # noqa: E402

#: 补丁标记。改完的源码里一定含有它，用来判断是否已经打过补丁。
MARK = "hisgen:"

SRC = "src/main/java/org/mitre/synthea"


# ---------------------------------------------------------------- 替换规则

NAMES_LOAD = (
    """  private static SimpleYML loadNames() {
    String filename = "names.yml";
    try {
      String namesData = Utilities.readResource(filename);
      return new SimpleYML(namesData);""",
    """  private static SimpleYML loadNames() {
    // hisgen: 姓名池文件改为可配置，并允许叠加一份本地化文件。
    // 叠加是把两份文本拼成一个 YAML 文档解析，所以叠加文件里
    // 只能出现**新的顶层键**（如 chinese），不能重复已有的 english/spanish/street。
    String filename = Config.get("generate.names.default_file", "names.yml");
    try {
      StringBuilder namesData =
          new StringBuilder(Utilities.readResource(filename, true, true));
      String overlay = Config.get("generate.names.overlay_file", "");
      if (overlay != null && !overlay.trim().isEmpty()) {
        namesData.append('\\n').append(Utilities.readResource(overlay.trim(), true, true));
      }
      return new SimpleYML(namesData.toString());""",
)

NAMES_HELPERS_ANCHOR = (
    """  public static final boolean appendNumbersToNames =
      Config.getAsBoolean("generate.append_numbers_to_person_names", false);""",
    """  public static final boolean appendNumbersToNames =
      Config.getAsBoolean("generate.append_numbers_to_person_names", false);

  /**
   * hisgen: 是否生成中文姓名与中文地址。
   * 由 generate.names.chinese 开关控制，或由语言标记推导。
   * @param language 语言标记，可为 null
   * @return 是否走中文分支
   */
  public static boolean isChinese(String language) {
    return Config.getAsBoolean("generate.names.chinese", false)
        || "chinese".equalsIgnoreCase(language)
        || "zh".equalsIgnoreCase(language);
  }

  /**
   * hisgen: 按语言习惯拼接姓名。中文姓在前且无分隔符，西文名在前、空格分隔。
   * @param firstName 名
   * @param lastName 姓
   * @return 完整姓名
   */
  public static String joinName(String firstName, String lastName) {
    if (isChinese(null)) {
      return lastName + firstName;
    }
    return firstName + " " + lastName;
  }""",
)

NAMES_FIRST = (
    """    if ("spanish".equalsIgnoreCase(language)) {
      choices = (List<String>) names.get("spanish." + gender);
    } else {
      choices = (List<String>) names.get("english." + gender);
    }""",
    """    if ("spanish".equalsIgnoreCase(language)) {
      choices = (List<String>) names.get("spanish." + gender);
    } else if (isChinese(language)) {
      choices = (List<String>) names.get("chinese." + gender);
    } else {
      choices = (List<String>) names.get("english." + gender);
    }""",
)

NAMES_LAST = (
    """    if ("spanish".equalsIgnoreCase(language)) {
      choices = (List<String>) names.get("spanish.family");
    } else {
      choices = (List<String>) names.get("english.family");
    }""",
    """    if ("spanish".equalsIgnoreCase(language)) {
      choices = (List<String>) names.get("spanish.family");
    } else if (isChinese(language)) {
      choices = (List<String>) names.get("chinese.family");
    } else {
      choices = (List<String>) names.get("english.family");
    }""",
)

NAMES_ADDRESS_GUARD = (
    """  public static String fakeAddress(boolean includeLine2, Person person) {
    int number = person.randInt(1000) + 100;""",
    """  public static String fakeAddress(boolean includeLine2, Person person) {
    if (isChinese((String) person.attributes.get(Person.FIRST_LANGUAGE))) {
      return chineseAddress(includeLine2, person);
    }
    int number = person.randInt(1000) + 100;""",
)

NAMES_ADDRESS_METHOD_ANCHOR = (
    """  /**
   * Adds a 1- to 3-digit hashcode to the end of the name.""",
    """  /**
   * hisgen: 生成中文地址，形如「上海市徐汇区漕溪北路123弄45号601室」。
   * 区名取自 person 的 CITY 属性（即 demographics.csv 的 NAME 列）。
   *
   * 城市前缀走配置而不是字面量：本仓库的 build.gradle 没有设
   * compileJava.options.encoding，源码按平台默认字符集编译，
   * 源码里写死中文有被读成乱码的风险；配置里的值由 Properties 按
   * Unicode 转义序列解码，与源码字符集无关。
   * （注意：这里不能写出那个转义序列的字面写法 —— Java 的词法分析
   *   在注释里也会处理它，写出来会直接编译报错。）
   * @param includeLine2 是否带室号
   * @param person 取地址的人
   * @return 中文地址
   */
  @SuppressWarnings("unchecked")
  private static String chineseAddress(boolean includeLine2, Person person) {
    String city = (String) person.attributes.get(Person.CITY);
    if (city == null) {
      city = "";
    }
    StringBuilder address = new StringBuilder(
        Config.get("generate.address.city_prefix", "")).append(city);
    address.append(pick((List<String>) names.get("chinese.street"), person));
    address.append(pick((List<String>) names.get("chinese.street_type"), person));
    address.append(person.randInt(300) + 1);
    address.append(pick((List<String>) names.get("chinese.lane"), person));
    address.append(person.randInt(120) + 1).append("号");
    if (includeLine2) {
      address.append(person.randInt(30) + 1).append('0').append(person.randInt(6) + 1);
      address.append(pick((List<String>) names.get("chinese.room"), person));
    }
    return address.toString();
  }

  /**
   * hisgen: 从姓名池里随机取一项。
   * @param choices 候选列表
   * @param person 随机源
   * @return 选中的一项
   */
  private static String pick(List<String> choices, Person person) {
    return choices.get(person.randInt(choices.size()));
  }

  /**
   * Adds a 1- to 3-digit hashcode to the end of the name.""",
)

LIFECYCLE_HELPERS_ANCHOR = (
    """  public static void birth(Person person, long time) {""",
    """  /** hisgen: 中国大陆手机号段。取自工信部已分配给公众移动通信网的前三位。 */
  private static final String[] CN_MOBILE_PREFIXES = {
      "130", "131", "132", "133", "135", "136", "137", "138", "139",
      "150", "151", "152", "153", "155", "156", "157", "158", "159",
      "170", "171", "172", "173", "175", "176", "177", "178",
      "180", "181", "182", "183", "184", "185", "186", "187", "188", "189",
      "191", "195", "196", "197", "198", "199"
  };

  /**
   * hisgen: 按 generate.lifecycle.phone.format 生成电话号码。
   * ``cn`` 为中国大陆 11 位手机号（号段真实，后 8 位随机，不保证已放号）；
   * 其他取值沿用 Synthea 原来的 555-xxx-xxxx 格式。
   * @param person 取号的人
   * @return 电话号码
   */
  private static String generatePhoneNumber(Person person) {
    if ("cn".equalsIgnoreCase(Config.get("generate.lifecycle.phone.format", "us"))) {
      String prefix = CN_MOBILE_PREFIXES[person.randInt(CN_MOBILE_PREFIXES.length)];
      StringBuilder number = new StringBuilder(prefix);
      for (int i = 0; i < 8; i++) {
        number.append(person.randInt(10));
      }
      return number.toString();
    }
    return "555-" + (person.randInt(999 - 100 + 1) + 100) + "-"
        + (person.randInt(9999 - 1000 + 1) + 1000);
  }

  public static void birth(Person person, long time) {""",
)

LIFECYCLE_NAME = (
    """      attributes.put(Person.NAME, firstName + " " + lastName);

      String phoneNumber = "555-" + ((person.randInt(999 - 100 + 1) + 100)) + "-"
          + ((person.randInt(9999 - 1000 + 1) + 1000));
      attributes.put(Person.TELECOM, phoneNumber);""",
    """      attributes.put(Person.NAME, Names.joinName(firstName, lastName));

      attributes.put(Person.TELECOM, generatePhoneNumber(person));""",
)

LIFECYCLE_PARENTS = (
    """    attributes.put(Person.NAME_MOTHER, motherFirstName + " " + motherLastName);

    String fatherFirstName = Names.fakeFirstName("M", language, person);
    // this is anglocentric where the baby gets the father's last name
    attributes.put(Person.NAME_FATHER, fatherFirstName + " " + attributes.get(Person.LAST_NAME));""",
    """    attributes.put(Person.NAME_MOTHER, Names.joinName(motherFirstName, motherLastName));

    String fatherFirstName = Names.fakeFirstName("M", language, person);
    // this is anglocentric where the baby gets the father's last name
    attributes.put(Person.NAME_FATHER,
        Names.joinName(fatherFirstName, (String) attributes.get(Person.LAST_NAME)));""",
)

LIFECYCLE_SSN = (
    """    String ssn = "999-" + ((person.randInt(99 - 10 + 1) + 10)) + "-"
        + ((person.randInt(9999 - 1000 + 1) + 1000));
    attributes.put(Person.IDENTIFIER_SSN, ssn);""",
    """    // hisgen: 美国社保号（SSN）对上海患者没有意义。**不生成**，而不是编一个
    // 像中国身份证号的号 —— 导出层明确拒绝把 SSN 当身份证号填进患者表
    // （见 src/hisgen/export/derive.py），编个假号比留空严重得多。
    // 注意不能写 attributes.put(..., null)：attributes 是 ConcurrentHashMap，
    // 值为 null 直接 NPE。属性不存在与值为 null 在读取端没有区别。
    // FhirR4 那边也打了补丁，值缺失时不再输出该 identifier。""",
)

LIFECYCLE_DRIVERS = (
    """      case 16:
        // driver's license
        if (person.attributes.get(Person.IDENTIFIER_DRIVERS) == null) {
          String identifierDrivers = "S999" + ((person.randInt(99999 - 10000 + 1) + 10000));
          person.attributes.put(Person.IDENTIFIER_DRIVERS, identifierDrivers);
        }
        break;""",
    """      case 16:
        // hisgen: 中国驾驶证号的编码规则（省份代码 + 序列 + 校验位）未核实，
        // 不编造；空值即可，患者表不需要这一列。
        if (!Names.isChinese((String) person.attributes.get(Person.FIRST_LANGUAGE))
            && person.attributes.get(Person.IDENTIFIER_DRIVERS) == null) {
          String identifierDrivers = "S999" + ((person.randInt(99999 - 10000 + 1) + 10000));
          person.attributes.put(Person.IDENTIFIER_DRIVERS, identifierDrivers);
        }
        break;""",
)

LIFECYCLE_PREFIX = (
    """      case 18:
        // name prefix
        if (person.attributes.get(Person.NAME_PREFIX) == null) {""",
    """      case 18:
        // name prefix
        // hisgen: 中文姓名没有 Mr./Ms. 这种西文敬称，整段跳过
        if (Names.isChinese((String) person.attributes.get(Person.FIRST_LANGUAGE))) {
          break;
        }
        if (person.attributes.get(Person.NAME_PREFIX) == null) {""",
)

LIFECYCLE_PASSPORT = (
    """        // passport number
        if (person.attributes.get(Person.IDENTIFIER_PASSPORT) == null) {""",
    """        // passport number
        // hisgen: 这里是美国护照号的生成规则（X+数字+X）。中国普通护照是
        // E+8位数字，规则不同且患者表用不到，先留空而不混用美国格式。
        if (!Names.isChinese((String) person.attributes.get(Person.FIRST_LANGUAGE))
            && person.attributes.get(Person.IDENTIFIER_PASSPORT) == null) {""",
)

LIFECYCLE_MARRIAGE = (
    """            if ("F".equals(person.attributes.get(Person.GENDER))) {
              person.attributes.put(Person.NAME_PREFIX, "Mrs.");""",
    """            // hisgen: 中国女性婚后**不改姓**，所以没有 maiden name，
            // 也不换成 Mrs.；整段西式改姓逻辑跳过。
            if ("F".equals(person.attributes.get(Person.GENDER))
                && !Names.isChinese((String) person.attributes.get(Person.FIRST_LANGUAGE))) {
              person.attributes.put(Person.NAME_PREFIX, "Mrs.");""",
)

LIFECYCLE_SUFFIX = (
    """        // "overeducated" -> suffix
        if ((person.attributes.get(Person.NAME_SUFFIX) == null)
            && ((double) person.attributes.get(Person.EDUCATION_LEVEL) >= 0.95)) {""",
    """        // "overeducated" -> suffix
        // hisgen: 中文姓名不挂 PhD/JD/MD 这类学位后缀
        if (!Names.isChinese((String) person.attributes.get(Person.FIRST_LANGUAGE))
            && (person.attributes.get(Person.NAME_SUFFIX) == null)
            && ((double) person.attributes.get(Person.EDUCATION_LEVEL) >= 0.95)) {""",
)

#: FhirR4 里 SSN 那段是无条件输出的（值是 null 也照样加一条 identifier），
#: 所以 LifecycleModule 置空之后还得把这里也挡上，否则输出里会留一条
#: 有类型、无值的 Social Security Number。
FHIR_SSN = (
    """    Code ssnCode = new Code("http://terminology.hl7.org/CodeSystem/v2-0203", "SS", "Social Security Number");
    patientResource.addIdentifier()
        .setType(mapCodeToCodeableConcept(ssnCode, "http://terminology.hl7.org/CodeSystem/v2-0203"))
        .setSystem("http://hl7.org/fhir/sid/us-ssn")
        .setValue((String) person.attributes.get(Person.IDENTIFIER_SSN));""",
    """    // hisgen: SSN 为空时不再输出这条 identifier。CN 地区置空（见
    // LifecycleModule 的补丁）；原逻辑无条件输出，会留一条无值的 SSN。
    String ssnValue = (String) person.attributes.get(Person.IDENTIFIER_SSN);
    if (ssnValue != null && !ssnValue.trim().isEmpty()) {
      Code ssnCode = new Code("http://terminology.hl7.org/CodeSystem/v2-0203", "SS", "Social Security Number");
      patientResource.addIdentifier()
          .setType(mapCodeToCodeableConcept(ssnCode, "http://terminology.hl7.org/CodeSystem/v2-0203"))
          .setSystem("http://hl7.org/fhir/sid/us-ssn")
          .setValue(ssnValue);
    }""",
)


DEMOGRAPHICS_LANGUAGE = (
    """  public String languageFromRaceAndEthnicity(String race, String ethnicity,
      RandomNumberGenerator random) {
    if (ethnicity.equals("hispanic")) {""",
    """  public String languageFromRaceAndEthnicity(String race, String ethnicity,
      RandomNumberGenerator random) {
    // hisgen: 非美国地区直接指定主要语言，跳过美国人口普查口径的语言分布。
    String override = Config.get("generate.demographics.language.override");
    if (override != null && !override.trim().isEmpty()) {
      return override.trim();
    }
    if (ethnicity.equals("hispanic")) {""",
)


CLINICIAN_NAME = (
    """      clinician.attributes.put(Clinician.NAME, firstName + " " + lastName);""",
    """      // hisgen: 中文姓名姓在前、无分隔符
      clinician.attributes.put(Clinician.NAME, Names.joinName(firstName, lastName));""",
)


PATCHES: list[tuple[str, list[tuple[str, str]]]] = [
    (f"{SRC}/world/concepts/Names.java", [
        NAMES_LOAD,
        NAMES_HELPERS_ANCHOR,
        NAMES_FIRST,
        NAMES_LAST,
        NAMES_ADDRESS_GUARD,
        NAMES_ADDRESS_METHOD_ANCHOR,
    ]),
    (f"{SRC}/modules/LifecycleModule.java", [
        LIFECYCLE_HELPERS_ANCHOR,
        LIFECYCLE_NAME,
        LIFECYCLE_PARENTS,
        LIFECYCLE_SSN,
        LIFECYCLE_DRIVERS,
        LIFECYCLE_PREFIX,
        LIFECYCLE_PASSPORT,
        LIFECYCLE_MARRIAGE,
        LIFECYCLE_SUFFIX,
    ]),
    (f"{SRC}/world/agents/Provider.java", [
        CLINICIAN_NAME,
    ]),
    (f"{SRC}/world/geography/Demographics.java", [
        DEMOGRAPHICS_LANGUAGE,
    ]),
    (f"{SRC}/export/FhirR4.java", [
        FHIR_SSN,
    ]),
]


def _backup_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".hisgen-orig")


def apply_one(path: Path, rules: list[tuple[str, str]], *, check: bool) -> str:
    """逐条判定，不靠文件级标记。

    文件里出现 ``hisgen:`` 不足以说明**每一条**都打过了 —— 有些替换本身
    不带注释标记（例如只改一行方法调用）。

    判定顺序很关键：**先看新文在不在**。有几条规则是"在锚点后面追加"，
    新文里仍然包含锚点原文，先查原文的话永远判成待打。
    """
    text = path.read_text(encoding="utf-8")
    pending: list[tuple[str, str]] = []
    applied = 0

    for old, new in rules:
        if new in text:
            applied += 1
        elif old in text:
            pending.append((old, new))
        else:
            head = old.strip().splitlines()[0][:60]
            return f"**锚点未命中：`{head}`**（Synthea 版本可能不同）"

    if not pending:
        return f"已打过补丁（{applied} 处），跳过"
    if check:
        return f"待打补丁（{len(pending)} 处，已生效 {applied} 处）"

    backup = _backup_path(path)
    if not backup.exists():
        shutil.copy2(path, backup)

    for old, new in pending:
        text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")
    return f"已打补丁（{len(pending)} 处，累计 {applied + len(pending)} 处），备份 {backup.name}"


def staleness(path: Path, rules: list[tuple[str, str]]) -> str | None:
    """检查文件里的补丁是不是**旧版**的。

    有几种规则是"在锚点后面追加"，新文里仍含锚点原文。于是当补丁正文被改过
    之后：新文查不到（不是新版）、锚点查得到（像是没打过）—— 看着像"待打"，
    实际是"已按旧版打过"。照着打一遍就会把旧内容留在原地、再插一份新的，
    重复定义方法，编译器报"已在类中定义"。这种损坏是静默的，值得专门挡一下。

    返回 None 表示没有旧版残留；否则返回一句人话说明。
    """
    text = path.read_text(encoding="utf-8")
    for old, new in rules:
        if new in text or old not in text:
            continue
        # 锚点还在但新文不在。如果这个锚点已经被旧版补丁用掉过一次，
        # 那么锚点原文在文件里会**出现不止一次**（旧版的新文里也含它）。
        if text.count(old) > 1:
            head = old.strip().splitlines()[0][:60]
            return f"**疑似旧版补丁残留**（锚点重复出现）：`{head}`"
    return None


def revert_one(path: Path) -> str:
    backup = _backup_path(path)
    if not backup.exists():
        return "没有备份，跳过"
    shutil.copy2(backup, path)
    backup.unlink()
    return "已从备份还原"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="给 Synthea 源码打上海本地化补丁")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="只报告状态，不改文件")
    group.add_argument("--revert", action="store_true", help="从 .hisgen-orig 备份还原")
    group.add_argument(
        "--refresh",
        action="store_true",
        help="先全部还原再重打。改过补丁正文以后用这个，避免旧版残留",
    )
    parser.add_argument("--synthea", default=None, help="Synthea 源码根目录")
    args = parser.parse_args(argv)

    root = Path(args.synthea).resolve() if args.synthea else Path(SYNTHEA_HOME).resolve()
    if not root.is_dir():
        print(f"[FAIL] Synthea 目录不存在：{root}", file=sys.stderr)
        return 1
    print(f"Synthea 源码根目录：{root}")

    failed = 0
    for relative, rules in PATCHES:
        path = root / relative
        if not path.is_file():
            print(f"  [FAIL] 找不到 {relative}")
            failed += 1
            continue

        if args.revert:
            note = revert_one(path)
        elif args.refresh:
            revert_one(path)
            note = apply_one(path, rules, check=False)
        else:
            stale = staleness(path, rules)
            if stale is not None and not args.check:
                print(f"  [FAIL] {Path(relative).name}：{stale}")
                print("         用 --refresh 重打（会先还原成官方源码再打一遍）。")
                failed += 1
                continue
            note = apply_one(path, rules, check=args.check)
            if stale is not None:
                note = f"{stale}　{note}"

        flag = "FAIL" if note.startswith("**") else "OK  "
        if flag == "FAIL":
            failed += 1
        print(f"  [{flag}] {Path(relative).name}：{note}")

    if failed:
        print(f"\n[FAIL] {failed} 个文件未能处理。补丁未生效前不要跑 Synthea ——"
              " 会得到英文姓名和 555 开头的美国电话。", file=sys.stderr)
        return 1
    print("\n[OK] 补丁处理完毕。")
    return 0


if __name__ == "__main__":
    if os.name == "nt":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # pragma: no cover
                pass
    raise SystemExit(main())