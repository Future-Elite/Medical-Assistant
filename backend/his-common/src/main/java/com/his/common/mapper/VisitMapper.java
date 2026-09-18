package com.his.common.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.his.common.entity.Visit;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Select;

import java.util.List;
import java.util.Map;

@Mapper
public interface VisitMapper extends BaseMapper<Visit> {

    /**
     * 按月统计就诊量，交给 MySQL 做 GROUP BY，避免把全表拉进内存聚合。
     *
     * <p>别名刻意不带下划线（{@code d} / {@code c}），以免被
     * {@code mybatis-plus.configuration.map-underscore-to-camel-case} 改写 Map 的 key。
     *
     * @return 每行 {@code {"d": "yyyy-MM", "c": 就诊数}}，按月份升序
     */
    @Select("SELECT DATE_FORMAT(visit_time, '%Y-%m') AS d, COUNT(*) AS c "
            + "FROM visit GROUP BY d ORDER BY d")
    List<Map<String, Object>> selectMonthlyCounts();
}
