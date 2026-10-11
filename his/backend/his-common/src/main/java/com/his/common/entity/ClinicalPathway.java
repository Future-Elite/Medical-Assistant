package com.his.common.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

@Data
@TableName("clinical_pathway")
public class ClinicalPathway {
    @TableId(type = IdType.AUTO)
    private Long id;
    private String diagnosisCode;
    private String diagnosisName;
    private String steps;
    private Integer enabled;
}
