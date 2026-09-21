#!/usr/bin/env bash
# 医学真实感改造 —— 本体数据灌库脚本（计划中的 D-4 批次）
#
# 作用：把「字典 / 知识本体」数据按固定顺序灌进 his_demo。
# 本脚本 **只增不删业务数据**：不 TRUNCATE、不碰 patient / visit / prescription
# 等任何业务表，也不调用 his-generator。
#
# 用法：
#   MYSQL_PWD=<口令> backend/sql/deploy-ontology.sh
# 口令不写在本文件里：mysql 客户端原生识别 MYSQL_PWD，也可改用 ~/.my.cnf。
# 可选环境变量：MYSQL_BIN / MYSQL_USER / MYSQL_DB / MYSQL_HOST
#
# 为什么 schema.sql 与 seed.sql 不在本脚本内：
#   两者都已执行过（23 张表已建、drug 的 id 1~12 已灌）。重跑 schema.sql 会
#   撞已有表，重跑 seed.sql 会重复插入基础 12 种药，故一律不重跑。
#
# 顺序不可调换：
#   1. seed-extension.sql —— rational_rule / clinical_pathway
#      **无 DELETE、无唯一键：只能灌一次**，重复执行会静默插入重复行。
#      它引用的药品 id 全部 ≤ 12（1 阿莫西林 / 3 布洛芬 / 4 硝苯地平 /
#      5 二甲双胍 / 8 左氧氟沙星 / 9 阿司匹林 / 10 氨氯地平），
#      与 seed-ontology.sql 的 §1（id 13~196）之间没有先后依赖。
#   2. seed-ontology.sql —— §1 药品字典扩充 id 13~196（带显式 id，重复执行
#      报主键冲突，不会静默重复）；§2~§6 各自在 INSERT 前 DELETE，可重复执行。
#      前置条件是 seed.sql 与 seed-extension.sql 已灌。
#
# 任何一步报错立即中止（不使用 --force）——静默跳过错误正是要避免的。

set -euo pipefail

MYSQL_BIN="${MYSQL_BIN:-mysql}"
MYSQL_USER="${MYSQL_USER:-root}"
MYSQL_DB="${MYSQL_DB:-his_demo}"

SQL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -z "${MYSQL_PWD:-}" ] && [ ! -f "$HOME/.my.cnf" ]; then
  echo "缺少凭据：请设 MYSQL_PWD=<口令>，或先建好 ~/.my.cnf。" >&2
  echo "本脚本不接收明文口令参数，也不会把口令写进任何文件。" >&2
  exit 1
fi

mysql_run() {
  if [ -n "${MYSQL_HOST:-}" ]; then
    "$MYSQL_BIN" -h"$MYSQL_HOST" -u"$MYSQL_USER" --default-character-set=utf8mb4 "$@"
  else
    "$MYSQL_BIN" -u"$MYSQL_USER" --default-character-set=utf8mb4 "$@"
  fi
}

count() {
  mysql_run -N -B "$MYSQL_DB" -e "$1"
}

# seed-extension.sql 只能灌一次，先拦重复执行。
already="$(count 'SELECT COUNT(*) FROM rational_rule;')"
if [ "$already" != "0" ]; then
  echo "rational_rule 已有 $already 行 —— seed-extension.sql 只能灌一次，" >&2
  echo "重复灌会静默插入重复行（该表无唯一键）。" >&2
  echo "确实要重灌的话，先执行：" >&2
  echo "  DELETE FROM rational_rule; DELETE FROM clinical_pathway;" >&2
  echo "再重跑本脚本。" >&2
  exit 1
fi

echo "==> [1/2] seed-extension.sql（rational_rule / clinical_pathway）"
mysql_run "$MYSQL_DB" < "$SQL_DIR/seed-extension.sql"

echo "==> [2/2] seed-ontology.sql（§1 药品字典 + §2~§6 本体）"
mysql_run "$MYSQL_DB" < "$SQL_DIR/seed-ontology.sql"

echo
echo "==> 灌库后行数"
mysql_run -t "$MYSQL_DB" -e "
SELECT 'drug                 §1 扩到 196' AS 表, COUNT(*) AS 行数 FROM drug
UNION ALL SELECT 'rule_config          seed.sql', COUNT(*) FROM rule_config
UNION ALL SELECT 'rational_rule        ext §1~2', COUNT(*) FROM rational_rule
UNION ALL SELECT 'clinical_pathway     ext §3', COUNT(*) FROM clinical_pathway
UNION ALL SELECT 'visit_rate           onto §2', COUNT(*) FROM visit_rate
UNION ALL SELECT 'dept_diagnosis       onto §3', COUNT(*) FROM dept_diagnosis
UNION ALL SELECT 'diagnosis_lab        onto §4', COUNT(*) FROM diagnosis_lab
UNION ALL SELECT 'diagnosis_operation  onto §5', COUNT(*) FROM diagnosis_operation
UNION ALL SELECT 'diagnosis_drug       onto §6', COUNT(*) FROM diagnosis_drug
UNION ALL SELECT 'drug_dose            onto §7（未编写）', COUNT(*) FROM drug_dose;"
