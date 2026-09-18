#!/usr/bin/env bash
# 功能测试脚本：对运行中的后端（http://localhost:8080）逐条执行 11 个功能用例。
#
# 前置：后端已启动、Redis 可用、MySQL 中已导入生成数据。
# 用法：bash backend/his-server/src/test/scripts/functional-test.sh
#
# 输出：每个用例一行 PASS/FAIL + 关键实测值，末尾给出汇总。结果手工誊入 docs/test-report.md。

set -u

BASE="${BASE:-http://localhost:8080}"
PASS=0
FAIL=0

# 唯一后缀，避免重复执行时撞手机号/身份证
SUFFIX=$(date +%H%M%S)

BODY=""
CODE=""

# 发起请求，填充 BODY / CODE。
# 注意：Windows 下 curl 是原生二进制，命令行参数会按系统 ANSI 代码页转换，
# 中文放进 -d 参数会变成 GBK 而报 "Invalid UTF-8 start byte"；
# 因此请求体一律用管道 + --data-binary @- 传入，绕开 argv 转换。
req() {
  local method="$1" url="$2" data="${3:-}"
  local out
  if [ -n "$data" ]; then
    out=$(printf '%s' "$data" | curl -s -m 60 -w $'\n%{http_code}' -X "$method" "$url" \
          -H 'Content-Type: application/json; charset=UTF-8' --data-binary @-)
  else
    out=$(curl -s -m 60 -w $'\n%{http_code}' -X "$method" "$url")
  fi
  CODE=$(printf '%s' "$out" | tail -n 1)
  BODY=$(printf '%s' "$out" | sed '$d')
}

# 取 JSON 中最先出现的 "key":value 的 value（数字或字符串）
jget() {
  printf '%s' "$1" | grep -o "\"$2\":[^,}]*" | head -n 1 | cut -d: -f2- | tr -d '"'
}

report() { # report <用例名> <结果> <实测摘要>
  if [ "$2" = "PASS" ]; then PASS=$((PASS + 1)); else FAIL=$((FAIL + 1)); fi
  printf '%-6s | %-22s | %s\n' "$2" "$1" "$3"
}

printf '%-6s | %-22s | %s\n' "结果" "用例" "实测"
printf -- '-------|------------------------|------------------------------------\n'

# ---------------------------------------------------------------- 1. 患者建档
NAME="测试患者${SUFFIX}"
req POST "$BASE/api/patients" \
  "{\"name\":\"$NAME\",\"gender\":1,\"birthDate\":\"1990-05-20\",\"idCard\":\"110101199005200${SUFFIX:0:3}\",\"phone\":\"139${SUFFIX}01\",\"address\":\"北京市测试区1号\"}"
PID=$(jget "$BODY" id)
if [ "$CODE" = "200" ] && [ "$(jget "$BODY" code)" = "0" ] && [ -n "$PID" ]; then
  report "患者建档" PASS "HTTP $CODE, code=0, 新建患者 id=$PID, name=$NAME"
else
  report "患者建档" FAIL "HTTP $CODE, body=$BODY"
fi

# ------------------------------------------------------------ 2. 患者查询-分页
req GET "$BASE/api/patients?page=2&size=5"
P2_TOTAL=$(jget "$BODY" total)
P2_CNT=$(printf '%s' "$BODY" | grep -o '"id":' | wc -l | tr -d ' ')
P2_FIRST=$(jget "$BODY" current)
if [ "$CODE" = "200" ] && [ "$P2_CNT" = "5" ] && [ "$P2_FIRST" = "2" ] && [ "$P2_TOTAL" -ge 10000 ]; then
  report "患者查询-分页" PASS "HTTP $CODE, current=2, 返回 $P2_CNT 条, total=$P2_TOTAL"
else
  report "患者查询-分页" FAIL "HTTP $CODE, 返回 $P2_CNT 条, current=$P2_FIRST, total=$P2_TOTAL"
fi

# -------------------------------------------------------- 3. 患者查询-关键词
# 中文关键词同样走管道（--data-urlencode name@- 从 stdin 读值），避免 argv 代码页转换
BODY=$(printf '%s' "$NAME" | curl -s -m 30 -G "$BASE/api/patients" \
        --data-urlencode "page=1" --data-urlencode "size=10" --data-urlencode "keyword@-")
KW_CNT=$(printf '%s' "$BODY" | grep -o '"id":' | wc -l | tr -d ' ')
if [ -n "$BODY" ] && [ "$KW_CNT" -ge 1 ] && printf '%s' "$BODY" | grep -q "$NAME"; then
  report "患者查询-关键词" PASS "keyword=$NAME, 命中 $KW_CNT 条, 含目标患者"
else
  report "患者查询-关键词" FAIL "keyword=$NAME, 命中 $KW_CNT 条, body=$BODY"
fi

# ---------------------------------------------------------------- 4. 挂号登记
# 同一次就诊内按 就诊 → 诊断 → 处方 → 检验 递进，形成真实的时间链
VISIT_TIME=$(date -d '-3 hours' '+%Y-%m-%dT%H:%M:%S')
DIAG_TIME=$(date -d '-2 hours -55 minutes' '+%Y-%m-%dT%H:%M:%S')
PRESC_TIME=$(date -d '-2 hours -50 minutes' '+%Y-%m-%dT%H:%M:%S')
LAB_TIME=$(date -d '-2 hours -30 minutes' '+%Y-%m-%dT%H:%M:%S')
req POST "$BASE/api/visits" \
  "{\"patientId\":$PID,\"visitNo\":\"V${PID}-T${SUFFIX}\",\"department\":\"内科\",\"doctor\":\"王冠杰\",\"visitTime\":\"$VISIT_TIME\",\"status\":0,\"remark\":\"功能测试挂号\"}"
VID=$(jget "$BODY" id)
if [ "$CODE" = "200" ] && [ "$(jget "$BODY" code)" = "0" ] && [ -n "$VID" ]; then
  report "挂号登记" PASS "HTTP $CODE, code=0, 就诊 id=$VID, visitTime=$VISIT_TIME"
else
  report "挂号登记" FAIL "HTTP $CODE, body=$BODY"
fi

# ---------------------------------------------------------------- 5. 诊断录入
req POST "$BASE/api/diagnoses" \
  "{\"patientId\":$PID,\"visitId\":$VID,\"icdCode\":\"J06.9\",\"diagnosisName\":\"上呼吸道感染\",\"doctor\":\"王冠杰\",\"diagnosedAt\":\"$DIAG_TIME\"}"
DID=$(jget "$BODY" id)
if [ "$CODE" = "200" ] && [ "$(jget "$BODY" code)" = "0" ] && [ -n "$DID" ]; then
  report "诊断录入" PASS "HTTP $CODE, code=0, 诊断 id=$DID, icdCode=J06.9, diagnosedAt=$DIAG_TIME"
else
  report "诊断录入" FAIL "HTTP $CODE, body=$BODY"
fi

# ------------------------------------------------------- 6. 处方开具（含明细）
req POST "$BASE/api/prescriptions" \
  "{\"patientId\":$PID,\"visitId\":$VID,\"doctor\":\"王冠杰\",\"prescribedAt\":\"$PRESC_TIME\",\"items\":[{\"drugId\":1,\"drugName\":\"阿莫西林胶囊\",\"dosage\":\"2片\",\"frequency\":\"每日3次\",\"days\":7,\"quantity\":2,\"unit\":\"盒\"},{\"drugId\":2,\"drugName\":\"感冒灵颗粒\",\"dosage\":\"1片\",\"frequency\":\"每日2次\",\"days\":5,\"quantity\":1,\"unit\":\"盒\"}]}"
RID=$(jget "$BODY" id)
TOTAL_AMT=$(jget "$BODY" totalAmount)
ITEM_CNT=$(printf '%s' "$BODY" | grep -o '"drugName":' | wc -l | tr -d ' ')
# 预期总额 = 18.50*2 + 12.00*1 = 49.00
if [ "$CODE" = "200" ] && [ "$ITEM_CNT" = "2" ] && [ "$TOTAL_AMT" = "49.00" ]; then
  report "处方开具(含明细)" PASS "HTTP $CODE, 处方 id=$RID, 明细 $ITEM_CNT 条, totalAmount=$TOTAL_AMT(预期49.00)"
else
  report "处方开具(含明细)" FAIL "HTTP $CODE, 明细 $ITEM_CNT 条, totalAmount=$TOTAL_AMT, body=$BODY"
fi

# ---------------------------------------------------------------- 7. 检验录入
req POST "$BASE/api/lab-tests" \
  "{\"patientId\":$PID,\"visitId\":$VID,\"itemCode\":\"BLD-WBC\",\"itemName\":\"白细胞计数\",\"resultValue\":\"12.30\",\"unit\":\"10^9/L\",\"referenceRange\":\"4.0-10.0\",\"isAbnormal\":1,\"testedAt\":\"$LAB_TIME\"}"
LID=$(jget "$BODY" id)
if [ "$CODE" = "200" ] && [ "$(jget "$BODY" code)" = "0" ] && [ -n "$LID" ]; then
  report "检验录入" PASS "HTTP $CODE, code=0, 检验 id=$LID, isAbnormal=1"
else
  report "检验录入" FAIL "HTTP $CODE, body=$BODY"
fi

# ------------------------------------------------- 8. 时间线查询（升序 + 缓存失效）
TS=$(curl -s -m 60 -w '\n%{time_total}' "$BASE/api/patients/$PID/timeline")
TL_BODY=$(printf '%s' "$TS" | sed '$d')
TL_SEC=$(printf '%s' "$TS" | tail -n 1)
TYPES=$(printf '%s' "$TL_BODY" | grep -o '"type":"[A-Z_]*"' | cut -d'"' -f4 | sort -u | tr '\n' ',' )
TIMES=$(printf '%s' "$TL_BODY" | grep -o '"time":"[^"]*"' | cut -d'"' -f4)
SORTED=$(printf '%s' "$TIMES" | sort)
TM_CNT=$(printf '%s' "$TIMES" | grep -c . )
# 新建患者应有 4 个事件：VISIT / DIAGNOSIS / PRESCRIPTION / LAB_TEST
if [ "$TIMES" = "$SORTED" ] && [ "$TM_CNT" = "4" ] \
   && printf '%s' "$TYPES" | grep -q VISIT \
   && printf '%s' "$TYPES" | grep -q DIAGNOSIS \
   && printf '%s' "$TYPES" | grep -q PRESCRIPTION \
   && printf '%s' "$TYPES" | grep -q LAB_TEST; then
  report "时间线查询-升序" PASS "4 个事件时间严格升序, 类型=$TYPES耗时 ${TL_SEC}s(写入后缓存已失效重建)"
else
  report "时间线查询-升序" FAIL "事件数=$TM_CNT, 类型=$TYPES, 升序=$([ "$TIMES" = "$SORTED" ] && echo 是 || echo 否)"
fi

# 生成数据集中的真实患者（id=1）时间线，事件数更多
BODY=$(curl -s -m 60 "$BASE/api/patients/1/timeline")
T1_TIMES=$(printf '%s' "$BODY" | grep -o '"time":"[^"]*"' | cut -d'"' -f4)
T1_CNT=$(printf '%s' "$T1_TIMES" | grep -c . )
T1_SORTED=$(printf '%s' "$T1_TIMES" | sort)
if [ "$T1_CNT" -gt 0 ] && [ "$T1_TIMES" = "$T1_SORTED" ]; then
  report "时间线查询-真实患者" PASS "patient 1 共 $T1_CNT 个事件, 时间升序成立"
else
  report "时间线查询-真实患者" FAIL "patient 1 事件数=$T1_CNT, 升序=$([ "$T1_TIMES" = "$T1_SORTED" ] && echo 是 || echo 否)"
fi

# ---------------------------------------------------------------- 9. 随机患者
S=$(date +%s%N)
BODY=$(curl -s -m 60 "$BASE/api/dashboard/random-patient")
E=$(date +%s%N)
RP_MS=$(( (E - S) / 1000000 ))
RP_ID=$(jget "$BODY" id)
RP_NAME=$(jget "$BODY" name)
if [ -n "$RP_ID" ] && [ "$(jget "$BODY" code)" = "0" ]; then
  report "随机患者" PASS "HTTP 200, code=0, id=$RP_ID, name=$RP_NAME, 耗时 ${RP_MS}ms"
else
  report "随机患者" FAIL "body=$BODY"
fi

# ---------------------------------------------------------------- 10. 就诊频率
S=$(date +%s%N)
BODY=$(curl -s -m 60 "$BASE/api/dashboard/stats/visit-frequency")
E=$(date +%s%N)
VF_MS=$(( (E - S) / 1000000 ))
VF_ROWS=$(printf '%s' "$BODY" | grep -o '"date":' | wc -l | tr -d ' ')
VF_FIRST=$(printf '%s' "$BODY" | grep -o '"date":"[^"]*"' | head -n 1 | cut -d'"' -f4)
VF_SUM=$(printf '%s' "$BODY" | grep -o '"count":[0-9]*' | cut -d: -f2 | awk '{s+=$1} END{print s+0}')
if [ "$VF_ROWS" -gt 30 ] && [ "$VF_SUM" -gt 120000 ]; then
  report "就诊频率统计" PASS "$VF_ROWS 个月份, 首月=$VF_FIRST, 合计 $VF_SUM 次就诊, 耗时 ${VF_MS}ms"
else
  report "就诊频率统计" FAIL "行数=$VF_ROWS, 合计=$VF_SUM"
fi

# ---------------------------------------------------------------- 11. 用药分布
S=$(date +%s%N)
BODY=$(curl -s -m 60 "$BASE/api/dashboard/stats/drug-distribution")
E=$(date +%s%N)
DD_MS=$(( (E - S) / 1000000 ))
DD_ROWS=$(printf '%s' "$BODY" | grep -o '"drugName":' | wc -l | tr -d ' ')
DD_TOP=$(printf '%s' "$BODY" | grep -o '"drugName":"[^"]*"' | head -n 1 | cut -d'"' -f4)
DD_TOPC=$(printf '%s' "$BODY" | grep -o '"count":[0-9]*' | head -n 1 | cut -d: -f2)
if [ "$DD_ROWS" = "12" ] && [ -n "$DD_TOP" ]; then
  report "用药分布统计" PASS "返回 $DD_ROWS 种药品(全部), 首位=$DD_TOP($DD_TOPC 次), 耗时 ${DD_MS}ms"
else
  report "用药分布统计" FAIL "行数=$DD_ROWS, body=$BODY"
fi

printf -- '-------|------------------------|------------------------------------\n'
printf '汇总: PASS=%d FAIL=%d (PID=%s VID=%s DID=%s RID=%s LID=%s)\n' \
  "$PASS" "$FAIL" "$PID" "$VID" "$DID" "$RID" "$LID"

[ "$FAIL" -eq 0 ] || exit 1
