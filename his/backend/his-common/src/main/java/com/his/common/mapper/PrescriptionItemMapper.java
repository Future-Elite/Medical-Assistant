package com.his.common.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.his.common.entity.PrescriptionItem;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

import java.util.List;
import java.util.Map;

@Mapper
public interface PrescriptionItemMapper extends BaseMapper<PrescriptionItem> {

    /**
     * 药品使用分布：按明细数降序取前 limit 种，聚合与排序都交给 MySQL。
     *
     * <p>与药品字典 JOIN 取规范名；并列时按药品名升序，保证结果稳定可复现。
     * 别名 {@code drugName} / {@code cnt} 不带下划线，避免被
     * {@code map-underscore-to-camel-case} 改写 Map 的 key。
     *
     * @return 每行 {@code {"drugName": 药品名, "cnt": 明细条数}}
     */
    @Select("SELECT d.name AS drugName, COUNT(*) AS cnt "
            + "FROM prescription_item pi JOIN drug d ON pi.drug_id = d.id "
            + "GROUP BY d.id, d.name ORDER BY cnt DESC, d.name ASC LIMIT #{limit}")
    List<Map<String, Object>> selectDrugDistribution(@Param("limit") int limit);
}
