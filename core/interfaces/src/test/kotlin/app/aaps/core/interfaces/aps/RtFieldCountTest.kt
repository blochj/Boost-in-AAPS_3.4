package app.aaps.core.interfaces.aps

import com.google.common.truth.Truth.assertThat
import org.junit.jupiter.api.Test
import java.lang.reflect.Modifier

/**
 * Pins the number of instance fields on [RT].
 *
 * RT is constructed inside determine_basal in every engine in this repository, and the largest of
 * those, DetermineBasalBoostV3MLG3.determine_basal, sits against the ART method verifier's ceiling.
 * One extra RT field costs one extra register in each of those methods, and the V3MLG3 one has no
 * headroom left: a build with 274 registers there crashed at startup on 2026-09-02, where 273 runs.
 * The failure gives no stack trace and no log line, because it happens before the app starts.
 *
 * So a new field here is not a local change to a data class. If telemetry needs to reach Nightscout,
 * append a "tag=value; " to the existing reason string instead, as the KAIROS twin, the tranche and
 * the shadow hypo score all do, and parse it in the extractor. That costs no registers.
 *
 * If this test fails because a field was added, do not simply update the number. Move the value into
 * the reason string. If a field genuinely has to exist, first rebuild and check with dexdump that
 * DetermineBasalBoostV3MLG3.determine_basal has not gone above 273 registers.
 */
class RtFieldCountTest {

    @Test fun rtCarriesNoMoreFieldsThanTheVerifierAllows() {
        val instanceFields = RT::class.java.declaredFields.count { !Modifier.isStatic(it.modifiers) }
        assertThat(instanceFields).isEqualTo(EXPECTED_INSTANCE_FIELDS)
    }

    companion object {

        // Measured on the build that runs. Raising this number has crashed the app before.
        private const val EXPECTED_INSTANCE_FIELDS = 126
    }
}
