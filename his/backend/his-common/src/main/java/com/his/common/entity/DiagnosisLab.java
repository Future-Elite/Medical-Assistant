package com.his.common.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.math.BigDecimal;

/** 诊断→检验本体表：本表自身即检验项目池，不存在全局随机抽取的化验池。 */
@Data
@TableName("diagnosis_lab")
public class DiagnosisLab {
    @TableId(type = IdType.AUTO)
    private Long id;
    private String icdCode;
    /** 可为 null，表示该行对全部科室适用；非空表示仅该科室适用。匹配规则见 OntologyRules.select。 */
    private String department;
    private String itemCode;
    private String itemName;
    private String unit;
    private String refRange;
    private String abnormalDirection;
    private BigDecimal abnormalProb;
    private BigDecimal weight;
    private Integer required;
}
