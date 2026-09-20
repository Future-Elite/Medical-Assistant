package com.his.common.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import java.time.LocalDateTime;

@Data
@TableName("lab_test")
public class LabTest {
    @TableId(type = IdType.AUTO)
    private Long id;
    private Long patientId;
    private Long visitId;
    private Long admissionId;
    private String itemCode;
    private String itemName;
    private String resultValue;
    private String unit;
    private String referenceRange;
    private Integer isAbnormal;
    private LocalDateTime testedAt;
}
