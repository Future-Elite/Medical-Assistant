package com.his.common.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import java.math.BigDecimal;

@Data
@TableName("drug")
public class Drug {
    @TableId(type = IdType.AUTO)
    private Long id;
    private String name;
    private String category;
    private String spec;
    private String unit;
    private BigDecimal price;
    /** 药理分类（ATC 第 4 层口径）。同类药同方互斥，取值见 seed-drug-class.sql。 */
    private String drugClass;
}
