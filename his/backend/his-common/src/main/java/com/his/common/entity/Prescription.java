package com.his.common.entity;

import com.baomidou.mybatisplus.annotation.*;
import lombok.Data;
import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.List;

@Data
@TableName("prescription")
public class Prescription {
    @TableId(type = IdType.AUTO)
    private Long id;
    private Long patientId;
    private Long visitId;
    private String doctor;
    private LocalDateTime prescribedAt;
    private BigDecimal totalAmount;
    @TableField(exist = false)
    private List<PrescriptionItem> items;
}
