package com.his.common.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.math.BigDecimal;

/** 诊断→药品本体表：决定某个诊断该开哪些药。 */
@Data
@TableName("diagnosis_drug")
public class DiagnosisDrug {
    @TableId(type = IdType.AUTO)
    private Long id;
    private String icdCode;
    /** 可为 null，表示该行对全部科室适用；非空表示仅该科室适用。匹配规则见 OntologyRules.select。 */
    private String department;
    private Long drugId;
    private Integer line;
    private BigDecimal weight;
    private Integer postop;
}
