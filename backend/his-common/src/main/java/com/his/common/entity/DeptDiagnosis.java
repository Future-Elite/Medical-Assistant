package com.his.common.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.math.BigDecimal;

/** 科室↔诊断本体表：决定「哪个诊断属于哪个科室、适用于什么年龄性别」。 */
@Data
@TableName("dept_diagnosis")
public class DeptDiagnosis {
    @TableId(type = IdType.AUTO)
    private Long id;
    private String department;
    private String icdCode;
    private String diagnosisName;
    /** 0不限1仅男2仅女。**与 `Patient.gender`（0男1女）不是同一个域**，勿跨表套用。 */
    private Integer gender;
    private Integer minAge;
    private Integer maxAge;
    private Integer chronic;
    private Integer admissionEligible;
    private BigDecimal weight;
    /** JSON 文本，长度 12 的正数数组；由调用方用 ObjectMapper 解析。**列可为 NULL，表示无季节性（不是全 1）**。 */
    private String monthWeights;
    private BigDecimal prevalence;
}
