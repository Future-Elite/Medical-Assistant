package com.his.common.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

@Data
@TableName("rule_config")
public class RuleConfig {
    @TableId(type = IdType.AUTO)
    private Long id;
    private String diagnosisCode;
    private String diagnosisName;
    private String drugIds;
    private Integer enabled;
}
