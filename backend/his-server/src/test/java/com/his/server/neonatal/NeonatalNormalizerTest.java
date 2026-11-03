package com.his.server.neonatal;

import org.junit.jupiter.api.Test;
import java.time.LocalDateTime;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

class NeonatalNormalizerTest {
    @Test void parsesOnlyUnambiguousDates() {
        assertEquals(LocalDateTime.of(2024, 6, 2, 0, 0), NeonatalNormalizer.date("2024.6.2"));
        assertEquals(LocalDateTime.of(2024, 6, 2, 9, 5), NeonatalNormalizer.date("2024.6.2 9：5"));
        assertEquals(LocalDateTime.of(2024, 6, 2, 9, 5), NeonatalNormalizer.date("2024.6.2 9.05"));
        assertNull(NeonatalNormalizer.date("20224.6.2"));
        assertNull(NeonatalNormalizer.date("2024.02.30"));
        assertNull(NeonatalNormalizer.date("2024.6.2下午"));
    }
    @Test void unknownGestationAndAmbiguousAgeNeverBecomeZeroOrBirthDate() {
        var n = NeonatalNormalizer.normalize(Map.of("孕周", "无提及孕周", "年龄", "12"), false);
        assertNull(n.weeks()); assertNull(n.days()); assertNull(n.ageDays()); assertNull(n.stay());
        assertTrue(n.issues().stream().anyMatch(i -> i.code().equals("UNKNOWN_GESTATION")));
    }
    @Test void keepsUnspecifiedDaysUnknownAndUsesCalendarDaysForStay() {
        Map<String, String> row = new HashMap<>(Map.of("孕周", "35", "年龄", "83 天", "入院时间", "2024.6.2 23:50", "出院时间", "2024.6.3"));
        var n = NeonatalNormalizer.normalize(row, true);
        assertEquals(35, n.weeks()); assertNull(n.days()); assertEquals(83, n.ageDays()); assertEquals(1, n.stay());
        row.put("孕周", "35+6"); assertEquals(6, NeonatalNormalizer.normalize(row, false).days());
        row.put("孕周", "35+7"); assertNull(NeonatalNormalizer.normalize(row, false).weeks());
        row.put("出院时间", "2024.6.1");
        n = NeonatalNormalizer.normalize(row, false);
        assertNull(n.stay()); assertTrue(n.issues().stream().anyMatch(i -> i.code().equals("REVERSED_DATES")));
    }
    @Test void broadOutcomeDefinitionsAreNotConfirmedTransferOrDeath() {
        assertEquals("其他/待核对", NeonatalNormalizer.outcome("未愈（包括无变化，恶化，检查未治，转院，自动出院）"));
        assertEquals("转院", NeonatalNormalizer.outcome("未愈（转院）"));
        assertEquals("自动出院", NeonatalNormalizer.outcome("好转（自动出院）"));
        assertEquals("未记录", NeonatalNormalizer.outcome(""));
        assertEquals("治愈", NeonatalNormalizer.outcome("愈"));
    }
}
