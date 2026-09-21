package com.his.common.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.math.BigDecimal;

/** 诊断→手术本体表：决定某个诊断能不能做手术、做哪个术式、术后最少住几天。 */
@Data
@TableName("diagnosis_operation")
public class DiagnosisOperation {
    @TableId(type = IdType.AUTO)
    private Long id;
    private String icdCode;
    /** 可为 null，表示该行对全部科室适用；非空表示仅该科室适用。匹配规则见 OntologyRules.select。 */
    private String department;
    private String operationCode;
    private String operationName;
    private Integer operationLevel;
    private BigDecimal probability;
    private Integer minPostopDays;
    private BigDecimal weight;
}
